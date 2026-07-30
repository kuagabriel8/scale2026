"""
SightLine fraud feature-engineering pipeline.

Reads the cleaned CSV/Parquet exports produced by fraud_data_cleaning.py
(cleaned_data/*.parquet) and joins/derives one wide feature row per
COMPANY_ID and one per TRANSACTION_ID, implementing the deterministic
T01-T13 typology logic from topfraudandtables.json as computed columns.

This is the feature-engineering layer that sits between the cleaned data
and a model predict() call - per rpt_ml_training_cycle.txt's
recommendation, RPT (or any downstream model) should consume these
engineered typology flags/points as input features, not raw source
columns it re-derives itself. The output tables are the "rows" context
you'd hand to such a model.

Governance note (topfraudandtables.json): typology detection is
"deterministic weighted rules" - nothing here is a model prediction.
This script computes the auditable rule layer only.

Simplifications (documented inline, not silently assumed):
  - T01 structuring: matched only for CURRENCY_ORIGINAL == THRESHOLD_CURRENCY
    (USD in this environment) - SCREENING_RULES has no clean per-typology
    threshold linkage column, so STRUCTURING_THRESHOLD_USD below is a
    stand-in default (classic USD 10,000 CTR threshold), not sourced from
    a specific SCREENING_RULES row. Replace with the correct rule mapping
    once one exists.
  - T03/T04: per T04's own critical_note ("test cheaply with a 2-hop join
    ... before committing build time"), these use a direct pairwise/2-hop
    join rather than full n-hop recursive cycle search.
  - T09: fuzzy-matches BENEFICIARY_NAME against ENTITY_NAME only. ALIASES
    is 100% empty JSON arrays in this environment (see data_quality_report.txt)
    so the alias path contributes nothing yet - wired up in
    fraud_data_cleaning.py's exploded_aliases_view() for when it is populated.
  - T10: BIC-implied country requires mapping the BIC's alpha-2 code to the
    schema's alpha-3 COUNTRY_CODE. Only a small manual map is provided
    (built from the alpha-2/alpha-3 codes actually observed) - an
    unmapped BIC country is flagged as its own data-quality condition,
    not silently dropped.
"""

import os
from decimal import Decimal

import numpy as np
import pandas as pd
from rapidfuzz import fuzz, process

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "cleaned_data")
REPORT_FILE = os.path.join(SCRIPT_DIR, "fraud_feature_report.txt")

TYPOLOGY_POINTS = {
    "T01": 25, "T02": 20, "T03": 20, "T04": 30, "T05": 20, "T06": 25,
    "T07": 15, "T08": 30, "T09": 40, "T10": 20, "T11": 20, "T12": 15, "T13": 20,
}

# --- T01 Structuring params (topfraudandtables.json) ---
STRUCTURING_THRESHOLD_USD = 10_000  # stand-in default - see module docstring
STRUCTURING_BAND_FLOOR_PCT = 0.90
STRUCTURING_MIN_COUNT = 3
STRUCTURING_WINDOW_HOURS = 72

# --- T02 Velocity Spike params ---
VELOCITY_Z_THRESHOLD = 3.0
VELOCITY_DAILY_COUNT_MULTIPLIER = 1.5

# --- T03 Pass-through params ---
PASSTHROUGH_DWELL_DAYS = 5
PASSTHROUGH_INFLOW_RETENTION_PCT = 0.85

# --- T04 Round-tripping params ---
ROUNDTRIP_CYCLE_WINDOW_DAYS = 30
ROUNDTRIP_AMOUNT_TOLERANCE_PCT = 0.15

# --- T05 Shell Company params ---
SHELL_MAX_EMPLOYEES = 5
SHELL_MIN_REVENUE_USD = 5_000_000
SHELL_RECENT_INCORPORATION_MONTHS = 24

# --- T06 Nominee params ---
NOMINEE_MIN_COMPANIES_PER_OWNER = 3

# --- T07 Ownership Opacity params ---
OWNERSHIP_STALE_VERIFICATION_MONTHS = 24

# --- T08 PEP/Corruption params ---
CORRUPTION_INDEX_THRESHOLD = 60

# --- T09 Sanctions Alias params ---
SANCTIONS_FUZZY_THRESHOLD = 88

# --- T10 Jurisdiction Hopping: BIC alpha-2 -> schema alpha-3, observed codes only ---
BIC_ALPHA2_TO_ALPHA3 = {
    "US": "USA", "FR": "FRA", "DE": "DEU", "CH": "CHE", "SG": "SGP",
    # "RO" (Romania) deliberately omitted - not present in the 23-row
    # COUNTRIES reference table. Flagged as T10_BIC_COUNTRY_UNMAPPED below.
}

# --- T11 Corridor params ---
FATF_POINTS = {"BLACK_LIST": 40, "NON_COMPLIANT": 40, "GREY_LIST": 15, "MEMBER": 0}
SANCTIONED_JURISDICTION_POINTS = 40

# --- T12 New-counterparty Flooding params ---
NEW_COUNTERPARTY_PCT_THRESHOLD = 40
PERIOD_OVER_PERIOD_MULTIPLIER = 2.0

# --- T13 Dormant Reactivation params ---
DORMANT_MAX_TXN_COUNT = 2
REACTIVATION_MIN_AMOUNT_USD = 250_000


def load(name):
    """hana_ml returns HANA DECIMAL columns as Python Decimal objects (object
    dtype), which round-trip through parquet unchanged - coerce those to
    float64 so downstream arithmetic doesn't mix Decimal/float/int types."""
    df = pd.read_parquet(os.path.join(DATA_DIR, f"{name}.parquet"))
    for col in df.columns:
        if df[col].dtype == object:
            sample = df[col].dropna()
            if not sample.empty and isinstance(sample.iloc[0], Decimal):
                df[col] = df[col].astype(float)
    return df


class FeatureReport:
    def __init__(self):
        self.lines = []

    def section(self, title):
        self.lines.append("")
        self.lines.append("=" * 88)
        self.lines.append(title)
        self.lines.append("=" * 88)

    def row(self, label, count, total=None):
        if total:
            pct = (count / total * 100) if total else 0.0
            self.lines.append(f"  {label:<70} {count:>10,}  ({pct:5.2f}% of {total:,})")
        else:
            self.lines.append(f"  {label:<70} {count:>10,}")

    def note(self, text):
        self.lines.append(f"  NOTE: {text}")

    def dump(self):
        content = "\n".join(self.lines)
        print(content)
        with open(REPORT_FILE, "w", encoding="utf-8") as f:
            f.write(content + "\n")


# ---------------------------------------------------------------------------
# Company-level typology features (T05, T06, T07, T08)
# ---------------------------------------------------------------------------

def build_company_features(report: FeatureReport, as_of: pd.Timestamp) -> pd.DataFrame:
    companies = load("COMPANIES")
    owners = load("COMPANY_BENEFICIAL_OWNERS")
    owners_norm = load("COMPANY_BENEFICIAL_OWNERS_NORMALIZED")
    ownership_totals = load("COMPANY_BENEFICIAL_OWNERS_OWNERSHIP_TOTALS")
    countries = load("COUNTRIES")[["COUNTRY_ID", "CORRUPTION_INDEX"]]

    total = len(companies)
    report.section("COMPANY-LEVEL TYPOLOGY FEATURES")

    # --- T05 Shell Company Layering ---
    companies["T05_SUBSTANCE_FLAG"] = (
        companies["EMPLOYEE_COUNT"].fillna(np.inf) <= SHELL_MAX_EMPLOYEES
    ) & (
        companies["ANNUAL_REVENUE_USD"].fillna(0) >= SHELL_MIN_REVENUE_USD
    ) & (
        companies["INCORPORATION_COUNTRY_ID"] != companies["HEADQUARTERS_COUNTRY_ID"]
    )
    recent_cutoff = as_of - pd.DateOffset(months=SHELL_RECENT_INCORPORATION_MONTHS)
    companies["T05_RECENT_INCORPORATION_FLAG"] = (
        pd.to_datetime(companies["INCORPORATION_DATE"]) >= recent_cutoff
    )
    companies["T05_SHELL_FLAG"] = (
        companies["T05_SUBSTANCE_FLAG"] & companies["T05_RECENT_INCORPORATION_FLAG"]
    )
    report.row("T05: substance test (low staff/high revenue + country mismatch)",
               int(companies["T05_SUBSTANCE_FLAG"].sum()), total)
    report.row("T05: recently incorporated (<= %d months before as-of date)"
               % SHELL_RECENT_INCORPORATION_MONTHS,
               int(companies["T05_RECENT_INCORPORATION_FLAG"].sum()), total)
    report.row("T05: full shell-company flag (substance AND recent incorporation)",
               int(companies["T05_SHELL_FLAG"].sum()), total)
    if companies["T05_RECENT_INCORPORATION_FLAG"].sum() == 0:
        report.note("no company in this environment was incorporated within the recent-incorporation "
                     "window - INCORPORATION_DATE tops out well before the as-of date, so T05 currently "
                     "reduces to the substance test alone until more recent incorporations appear")

    # --- T06 Nominee / Shared-Owner Network ---
    name_company_counts = owners_norm.groupby("OWNER_NAME_NORMALIZED")["COMPANY_ID"].nunique()
    nominee_names = name_company_counts[name_company_counts >= NOMINEE_MIN_COMPANIES_PER_OWNER].index
    nominee_company_ids = set(
        owners_norm.loc[owners_norm["OWNER_NAME_NORMALIZED"].isin(nominee_names), "COMPANY_ID"]
    )
    companies["T06_NOMINEE_FLAG"] = companies["COMPANY_ID"].isin(nominee_company_ids)
    report.row(f"T06: companies linked to a shared owner (>= {NOMINEE_MIN_COMPANIES_PER_OWNER} companies)",
               int(companies["T06_NOMINEE_FLAG"].sum()), total)

    # --- T07 Ownership Opacity ---
    companies = companies.merge(ownership_totals, on="COMPANY_ID", how="left")
    companies["T07_DATA_ERROR_OVER100"] = companies["TOTAL_DECLARED_PCT"] > 100
    companies["T07_UNEXPLAINED_OWNERSHIP_FLAG"] = (
        (companies["TOTAL_DECLARED_PCT"] < 100) & ~companies["T07_DATA_ERROR_OVER100"]
    )
    latest_verified = owners.groupby("COMPANY_ID")["VERIFIED_DATE"].max()
    companies = companies.merge(
        latest_verified.rename("LATEST_OWNER_VERIFIED_DATE"), on="COMPANY_ID", how="left"
    )
    stale_cutoff = as_of - pd.DateOffset(months=OWNERSHIP_STALE_VERIFICATION_MONTHS)
    companies["T07_STALE_VERIFICATION_FLAG"] = (
        companies["LATEST_OWNER_VERIFIED_DATE"].isna()
        | (pd.to_datetime(companies["LATEST_OWNER_VERIFIED_DATE"]) < stale_cutoff)
    )
    companies["T07_OPACITY_FLAG"] = (
        companies["T07_UNEXPLAINED_OWNERSHIP_FLAG"] | companies["T07_STALE_VERIFICATION_FLAG"]
    )
    report.row("T07: data-error rows (SUM ownership% > 100, excluded from score)",
               int(companies["T07_DATA_ERROR_OVER100"].sum()), total)
    report.row("T07: unexplained ownership (SUM ownership% < 100)",
               int(companies["T07_UNEXPLAINED_OWNERSHIP_FLAG"].sum()), total)
    report.row("T07: stale/missing beneficial-owner verification (>= %d months)"
               % OWNERSHIP_STALE_VERIFICATION_MONTHS,
               int(companies["T07_STALE_VERIFICATION_FLAG"].sum()), total)

    # --- T08 Corruption / PEP Proceeds ---
    owners_c = owners.merge(
        countries.rename(columns={"COUNTRY_ID": "RESIDENCE_COUNTRY_ID",
                                    "CORRUPTION_INDEX": "RESIDENCE_CORRUPTION_INDEX"}),
        on="RESIDENCE_COUNTRY_ID", how="left",
    )
    pep_hits = (
        owners_c[(owners_c["IS_PEP"] == True)  # noqa: E712
                 & (owners_c["RESIDENCE_CORRUPTION_INDEX"] > CORRUPTION_INDEX_THRESHOLD)]
        .groupby("COMPANY_ID").size()
    )
    companies = companies.merge(pep_hits.rename("T08_PEP_HIT_COUNT"), on="COMPANY_ID", how="left")
    companies["T08_PEP_HIT_COUNT"] = companies["T08_PEP_HIT_COUNT"].fillna(0).astype(int)
    hq_corrupt = companies.merge(
        countries.rename(columns={"COUNTRY_ID": "HEADQUARTERS_COUNTRY_ID",
                                    "CORRUPTION_INDEX": "HQ_CORRUPTION_INDEX"}),
        on="HEADQUARTERS_COUNTRY_ID", how="left",
    )["HQ_CORRUPTION_INDEX"]
    companies["T08_COMPANY_PEP_HQ_RISK_FLAG"] = (
        companies["PEP_ASSOCIATED"].fillna(False) & (hq_corrupt > CORRUPTION_INDEX_THRESHOLD)
    )
    companies["T08_FLAG"] = (companies["T08_PEP_HIT_COUNT"] > 0) | companies["T08_COMPANY_PEP_HQ_RISK_FLAG"]
    report.row("T08: owner is PEP resident in high-corruption jurisdiction (index > %d)"
               % CORRUPTION_INDEX_THRESHOLD,
               int((companies["T08_PEP_HIT_COUNT"] > 0).sum()), total)
    report.row("T08: company PEP-associated + high-corruption HQ jurisdiction",
               int(companies["T08_COMPANY_PEP_HQ_RISK_FLAG"].sum()), total)
    report.row("T08: combined PEP/corruption flag", int(companies["T08_FLAG"].sum()), total)

    companies["COMPANY_TYPOLOGY_POINTS"] = np.clip(
        companies["T05_SHELL_FLAG"].astype(int) * TYPOLOGY_POINTS["T05"]
        + companies["T06_NOMINEE_FLAG"].astype(int) * TYPOLOGY_POINTS["T06"]
        + companies["T07_OPACITY_FLAG"].astype(int) * TYPOLOGY_POINTS["T07"]
        + companies["T08_FLAG"].astype(int) * TYPOLOGY_POINTS["T08"],
        0, 100,
    )
    report.row("companies with any company-level typology flag",
               int((companies["COMPANY_TYPOLOGY_POINTS"] > 0).sum()), total)

    return companies


# ---------------------------------------------------------------------------
# Transaction-level typology features
# ---------------------------------------------------------------------------

def select_baselines(baselines: pd.DataFrame) -> pd.DataFrame:
    """Most-recent BASELINE_PERIOD row per company, plus the prior period's
    NEW_COUNTERPARTY_PCT for T12's period-over-period comparison. Explicit
    row selection per T02's critical_note - never left ambiguous."""
    bl = baselines.sort_values(["COMPANY_ID", "PERIOD_END"])
    bl["rank_desc"] = bl.groupby("COMPANY_ID").cumcount(ascending=False)
    current = bl[bl["rank_desc"] == 0].drop(columns="rank_desc")
    prior = bl[bl["rank_desc"] == 1][["COMPANY_ID", "NEW_COUNTERPARTY_PCT"]].rename(
        columns={"NEW_COUNTERPARTY_PCT": "PRIOR_NEW_COUNTERPARTY_PCT"}
    )
    return current.merge(prior, on="COMPANY_ID", how="left")


def compute_t01_structuring(tx: pd.DataFrame, report: FeatureReport) -> pd.Series:
    in_band = (
        (tx["CURRENCY_ORIGINAL"] == "USD")
        & (tx["AMOUNT_ORIGINAL"] >= STRUCTURING_THRESHOLD_USD * STRUCTURING_BAND_FLOOR_PCT)
        & (tx["AMOUNT_ORIGINAL"] < STRUCTURING_THRESHOLD_USD)
    )
    band_tx = tx.loc[in_band, ["TRANSACTION_ID", "ORIGINATOR_COMPANY_ID", "INITIATED_AT"]].copy()

    def rolling_count(group):
        g = group.sort_values("INITIATED_AT")
        counts = (
            g.set_index("INITIATED_AT")["TRANSACTION_ID"]
            .rolling(f"{STRUCTURING_WINDOW_HOURS}h")
            .count()
        )
        return pd.Series(counts.values, index=g.index)

    if band_tx.empty:
        window_count = pd.Series(dtype=float)
    else:
        window_count = band_tx.groupby("ORIGINATOR_COMPANY_ID", group_keys=False).apply(rolling_count)
    band_tx["STRUCTURING_WINDOW_COUNT"] = window_count
    flag = tx["TRANSACTION_ID"].map(
        band_tx.set_index("TRANSACTION_ID")["STRUCTURING_WINDOW_COUNT"]
    ).fillna(0) >= STRUCTURING_MIN_COUNT

    report.row(f"T01: transactions in the {STRUCTURING_THRESHOLD_USD:,.0f}-band "
               f"(>= {STRUCTURING_BAND_FLOOR_PCT:.0%} of threshold, USD only)",
               int(in_band.sum()), len(tx))
    report.row(f"T01: structuring flag ({STRUCTURING_MIN_COUNT}+ band txns / "
               f"{STRUCTURING_WINDOW_HOURS}h per originator)", int(flag.sum()), len(tx))
    non_usd = (tx["CURRENCY_ORIGINAL"] != "USD").sum()
    if non_usd:
        report.note(f"{non_usd:,} non-USD transactions are excluded from T01 - no reliable "
                     "per-currency threshold is available from SCREENING_RULES in this environment")
    return flag


def compute_t02_velocity(tx: pd.DataFrame, current_bl: pd.DataFrame, report: FeatureReport):
    bl_cols = current_bl.set_index("COMPANY_ID")[
        ["AVG_AMOUNT_USD", "STDDEV_AMOUNT_USD", "MAX_DAILY_COUNT"]
    ]
    joined = tx[["TRANSACTION_ID", "ORIGINATOR_COMPANY_ID", "AMOUNT_USD", "INITIATED_AT"]].merge(
        bl_cols, left_on="ORIGINATOR_COMPANY_ID", right_index=True, how="left"
    )
    has_stddev = joined["STDDEV_AMOUNT_USD"].notna() & (joined["STDDEV_AMOUNT_USD"] > 0)
    z = pd.Series(np.nan, index=joined.index)
    z[has_stddev] = (
        (joined.loc[has_stddev, "AMOUNT_USD"] - joined.loc[has_stddev, "AVG_AMOUNT_USD"])
        / joined.loc[has_stddev, "STDDEV_AMOUNT_USD"]
    )
    velocity_flag = z > VELOCITY_Z_THRESHOLD

    txn_date = joined["INITIATED_AT"].dt.date
    daily_counts = joined.groupby(["ORIGINATOR_COMPANY_ID", txn_date])["TRANSACTION_ID"].transform("count")
    daily_flag = daily_counts > (joined["MAX_DAILY_COUNT"].fillna(np.inf) * VELOCITY_DAILY_COUNT_MULTIPLIER)

    report.row("T02: baseline z-score undefined (null/zero STDDEV) - excluded",
               int((~has_stddev).sum()), len(tx))
    report.row(f"T02: velocity spike flag (z > {VELOCITY_Z_THRESHOLD})", int(velocity_flag.sum()), len(tx))
    report.row(f"T02: daily count exceeds {VELOCITY_DAILY_COUNT_MULTIPLIER}x baseline max",
               int(daily_flag.fillna(False).sum()), len(tx))
    return z.values, velocity_flag.values, daily_flag.fillna(False).values


def compute_t03_passthrough(tx: pd.DataFrame, report: FeatureReport) -> pd.Series:
    """Trailing-window inbound vs. outbound sum per company, via a
    searchsorted/cumsum as-of window sum rather than a per-row loop."""
    inbound = tx.dropna(subset=["BENEFICIARY_COMPANY_ID"])[
        ["BENEFICIARY_COMPANY_ID", "INITIATED_AT", "AMOUNT_USD"]
    ].rename(columns={"BENEFICIARY_COMPANY_ID": "COMPANY_ID", "AMOUNT_USD": "IN_AMOUNT"})
    inbound = inbound.sort_values(["COMPANY_ID", "INITIATED_AT"])

    outbound = tx[["TRANSACTION_ID", "ORIGINATOR_COMPANY_ID", "INITIATED_AT", "AMOUNT_USD"]].rename(
        columns={"ORIGINATOR_COMPANY_ID": "COMPANY_ID"}
    )

    window = np.timedelta64(PASSTHROUGH_DWELL_DAYS, "D")
    flags = np.zeros(len(outbound), dtype=bool)
    outbound_pos = {idx: pos for pos, idx in enumerate(outbound.index)}

    for company_id, group in outbound.groupby("COMPANY_ID"):
        inb = inbound[inbound["COMPANY_ID"] == company_id]
        if inb.empty:
            continue
        times = inb["INITIATED_AT"].to_numpy(dtype="datetime64[ns]")
        cum_in = np.concatenate([[0.0], np.cumsum(inb["IN_AMOUNT"].to_numpy(dtype=float))])
        group_times = group["INITIATED_AT"].to_numpy(dtype="datetime64[ns]")
        for idx, t, out_amt in zip(group.index, group_times, group["AMOUNT_USD"].to_numpy(dtype=float)):
            lo = np.searchsorted(times, t - window, side="left")
            hi = np.searchsorted(times, t, side="right")
            inbound_sum = cum_in[hi] - cum_in[lo]
            if inbound_sum > 0 and out_amt >= PASSTHROUGH_INFLOW_RETENTION_PCT * inbound_sum:
                flags[outbound_pos[idx]] = True

    flag = pd.Series(flags, index=outbound.index).reindex(tx.index, fill_value=False)
    report.row(f"T03: pass-through flag (outbound >= {PASSTHROUGH_INFLOW_RETENTION_PCT:.0%} of "
               f"trailing {PASSTHROUGH_DWELL_DAYS}-day inbound)", int(flag.sum()), len(tx))
    return flag


def compute_t04_roundtrip(tx: pd.DataFrame, report: FeatureReport) -> pd.Series:
    """2-hop round-trip test (A->B, then B->A within the cycle window) -
    the cheap test T04's critical_note recommends before a full n-hop
    recursive cycle search."""
    edges = tx.dropna(subset=["BENEFICIARY_COMPANY_ID"])[
        ["TRANSACTION_ID", "ORIGINATOR_COMPANY_ID", "BENEFICIARY_COMPANY_ID", "AMOUNT_USD", "INITIATED_AT"]
    ]
    merged = edges.merge(
        edges,
        left_on=["ORIGINATOR_COMPANY_ID", "BENEFICIARY_COMPANY_ID"],
        right_on=["BENEFICIARY_COMPANY_ID", "ORIGINATOR_COMPANY_ID"],
        suffixes=("_OUT", "_RET"),
    )
    merged = merged[merged["INITIATED_AT_RET"] > merged["INITIATED_AT_OUT"]]
    days_to_return = (merged["INITIATED_AT_RET"] - merged["INITIATED_AT_OUT"]).dt.days
    merged = merged[days_to_return <= ROUNDTRIP_CYCLE_WINDOW_DAYS]
    ratio_diff = (merged["AMOUNT_USD_RET"] - merged["AMOUNT_USD_OUT"]).abs() / merged["AMOUNT_USD_OUT"]
    merged = merged[ratio_diff <= ROUNDTRIP_AMOUNT_TOLERANCE_PCT]

    flagged_ids = set(merged["TRANSACTION_ID_OUT"]) | set(merged["TRANSACTION_ID_RET"])
    flag = tx["TRANSACTION_ID"].isin(flagged_ids)
    report.row(f"T04: 2-hop round-trip legs (<= {ROUNDTRIP_CYCLE_WINDOW_DAYS}d, "
               f"<= {ROUNDTRIP_AMOUNT_TOLERANCE_PCT:.0%} amount variance)", int(flag.sum()), len(tx))
    report.note("this is the cheap 2-hop test only, per T04's critical_note - a full n-hop "
                 "recursive cycle search (A->B->C->A, max_hops=4) is not implemented here")
    return flag


def compute_t09_sanctions(tx: pd.DataFrame, report: FeatureReport) -> pd.DataFrame:
    sanctions = load("SANCTIONS_LISTS")
    unique_names = tx["BENEFICIARY_NAME"].dropna().unique().tolist()
    entity_names = sanctions["ENTITY_NAME"].tolist()

    if unique_names and entity_names:
        scores = process.cdist(unique_names, entity_names, scorer=fuzz.WRatio, workers=-1)
        best_idx = scores.argmax(axis=1)
        best_score = scores.max(axis=1)
    else:
        best_idx = np.array([], dtype=int)
        best_score = np.array([])

    matches = pd.DataFrame({
        "BENEFICIARY_NAME": unique_names,
        "T09_SANCTIONS_MATCH_SCORE": best_score,
        "T09_SANCTIONS_MATCH_ENTITY": [entity_names[i] for i in best_idx] if len(best_idx) else [],
    })
    matches["T09_SANCTIONS_FLAG"] = matches["T09_SANCTIONS_MATCH_SCORE"] >= SANCTIONS_FUZZY_THRESHOLD
    out = tx[["TRANSACTION_ID", "BENEFICIARY_NAME"]].merge(matches, on="BENEFICIARY_NAME", how="left")

    report.row(f"T09: beneficiary name fuzzy-matches a sanctioned entity (>= {SANCTIONS_FUZZY_THRESHOLD}%)",
               int(out["T09_SANCTIONS_FLAG"].fillna(False).sum()), len(tx))
    report.note("matches ENTITY_NAME only - ALIASES is empty in this environment (see "
                 "data_quality_report.txt / exploded_aliases_view in fraud_data_cleaning.py) and "
                 "does not filter by active/valid-at-transaction-date window yet")
    hit_rate = out["T09_SANCTIONS_FLAG"].fillna(False).mean()
    if hit_rate > 0.05:
        report.note(f"CAVEAT: a {hit_rate:.1%} hit rate at a {SANCTIONS_FUZZY_THRESHOLD}% threshold is high "
                     "for a real sanctions-evasion signal - rapidfuzz's WRatio scorer inflates scores for "
                     "short/generic names via partial-ratio boosting. Verify against known true/false "
                     "positives before trusting this at face value; consider a stricter scorer "
                     "(e.g. token_sort_ratio) or a higher threshold for production")
    return out[["TRANSACTION_ID", "T09_SANCTIONS_MATCH_SCORE", "T09_SANCTIONS_MATCH_ENTITY", "T09_SANCTIONS_FLAG"]]


def compute_t10_t11(tx: pd.DataFrame, report: FeatureReport) -> pd.DataFrame:
    countries = load("COUNTRIES")
    regions = load("REGIONS")
    bic = load("TRANSACTIONS_BIC_COUNTRY")
    c = countries.merge(regions[["REGION_ID", "IS_HIGH_RISK"]], on="REGION_ID", how="left")

    out = tx[["TRANSACTION_ID", "ORIGINATING_COUNTRY_ID", "DESTINATION_COUNTRY_ID",
              "BENEFICIARY_COUNTRY_ID"]].merge(bic, on="TRANSACTION_ID", how="left")

    out["BIC_ALPHA3"] = out["BIC_COUNTRY_CODE"].map(BIC_ALPHA2_TO_ALPHA3)
    out["T10_BIC_COUNTRY_UNMAPPED"] = out["BIC_COUNTRY_CODE"].notna() & out["BIC_ALPHA3"].isna()
    code_to_id = countries.set_index("COUNTRY_CODE")["COUNTRY_ID"]
    out["BIC_COUNTRY_ID"] = out["BIC_ALPHA3"].map(code_to_id)

    out["T10_JURISDICTION_HOP_FLAG"] = (
        out["BIC_COUNTRY_ID"].notna()
        & (
            (out["BIC_COUNTRY_ID"] != out["DESTINATION_COUNTRY_ID"])
            | (out["BIC_COUNTRY_ID"] != out["BENEFICIARY_COUNTRY_ID"])
        )
    )

    def leg_points(country_ids: pd.Series) -> pd.Series:
        legs = c.set_index("COUNTRY_ID").reindex(country_ids)
        fatf_pts = legs["FATF_STATUS"].map(FATF_POINTS).fillna(0)
        sanctioned_pts = legs["SANCTIONS_LIST"].fillna(False).astype(int) * SANCTIONED_JURISDICTION_POINTS
        return (fatf_pts + sanctioned_pts).values

    orig_pts = leg_points(out["ORIGINATING_COUNTRY_ID"])
    dest_pts = leg_points(out["DESTINATION_COUNTRY_ID"])
    bene_pts = leg_points(out["BENEFICIARY_COUNTRY_ID"])
    out["T11_CORRIDOR_POINTS"] = np.clip(np.maximum.reduce([orig_pts, dest_pts, bene_pts]), 0, 100)
    out["T11_HIGH_RISK_CORRIDOR_FLAG"] = out["T11_CORRIDOR_POINTS"] > 0

    region_high_risk = c.set_index("COUNTRY_ID")["IS_HIGH_RISK"]
    out["T11_ANY_LEG_HIGH_RISK_REGION"] = (
        out["ORIGINATING_COUNTRY_ID"].map(region_high_risk).fillna(False)
        | out["DESTINATION_COUNTRY_ID"].map(region_high_risk).fillna(False)
        | out["BENEFICIARY_COUNTRY_ID"].map(region_high_risk).fillna(False)
    )

    report.row("T10: BIC-implied country code not present in COUNTRIES reference table",
               int(out["T10_BIC_COUNTRY_UNMAPPED"].sum()), len(tx))
    if out["T10_BIC_COUNTRY_UNMAPPED"].sum():
        unmapped = sorted(out.loc[out["T10_BIC_COUNTRY_UNMAPPED"], "BIC_COUNTRY_CODE"].unique())
        report.note(f"unmapped BIC country code(s): {unmapped} - BIC implies a country outside "
                     "the 23-row COUNTRIES reference table used for corridor/jurisdiction scoring")
    report.row("T10: jurisdiction divergence (destination/beneficiary vs. BIC-implied country)",
               int(out["T10_JURISDICTION_HOP_FLAG"].sum()), len(tx))
    dest_bene_mismatch = (tx["DESTINATION_COUNTRY_ID"] != tx["BENEFICIARY_COUNTRY_ID"]).sum()
    if dest_bene_mismatch == 0:
        report.note("CAVEAT: DESTINATION_COUNTRY_ID == BENEFICIARY_COUNTRY_ID for every row in this "
                     "environment, so the divergence flag above is driven entirely by BIC-implied country "
                     "vs. those two (always-equal) fields. Only 6 distinct BIC country codes appear across "
                     "150,000 transactions - this produces a high, mechanically-driven hit rate here, not "
                     "necessarily a discriminating real-world signal. Validate against production BIC "
                     "diversity before trusting this flag's precision")
    report.row("T11: high-risk corridor flag (FATF grey/black or sanctioned jurisdiction, any leg)",
               int(out["T11_HIGH_RISK_CORRIDOR_FLAG"].sum()), len(tx))
    report.row("T11: any leg in a REGIONS.IS_HIGH_RISK region",
               int(out["T11_ANY_LEG_HIGH_RISK_REGION"].sum()), len(tx))

    return out[["TRANSACTION_ID", "T10_BIC_COUNTRY_UNMAPPED", "T10_JURISDICTION_HOP_FLAG",
                "T11_CORRIDOR_POINTS", "T11_HIGH_RISK_CORRIDOR_FLAG", "T11_ANY_LEG_HIGH_RISK_REGION"]]


def compute_t12_t13(tx: pd.DataFrame, current_bl: pd.DataFrame, companies: pd.DataFrame,
                     report: FeatureReport) -> pd.DataFrame:
    bl = current_bl.set_index("COMPANY_ID")
    joined = tx[["TRANSACTION_ID", "ORIGINATOR_COMPANY_ID", "AMOUNT_USD"]].merge(
        bl[["NEW_COUNTERPARTY_PCT", "PRIOR_NEW_COUNTERPARTY_PCT", "TRANSACTION_COUNT"]],
        left_on="ORIGINATOR_COMPANY_ID", right_index=True, how="left",
    )
    prior = joined["PRIOR_NEW_COUNTERPARTY_PCT"]
    joined["T12_FLOODING_FLAG"] = (
        (joined["NEW_COUNTERPARTY_PCT"] > NEW_COUNTERPARTY_PCT_THRESHOLD)
        & prior.notna()
        & (joined["NEW_COUNTERPARTY_PCT"] > prior * PERIOD_OVER_PERIOD_MULTIPLIER)
    )

    rel_start = companies.set_index("COMPANY_ID")["RELATIONSHIP_START_DATE"]
    joined = joined.merge(rel_start.rename("RELATIONSHIP_START_DATE"),
                           left_on="ORIGINATOR_COMPANY_ID", right_index=True, how="left")
    joined["T13_DORMANT_BASELINE_FLAG"] = joined["TRANSACTION_COUNT"].fillna(np.inf) <= DORMANT_MAX_TXN_COUNT
    joined["T13_REACTIVATION_FLAG"] = (
        joined["T13_DORMANT_BASELINE_FLAG"] & (joined["AMOUNT_USD"] >= REACTIVATION_MIN_AMOUNT_USD)
    )

    report.row(f"T12: new-counterparty flooding (pct > {NEW_COUNTERPARTY_PCT_THRESHOLD}% and "
               f">= {PERIOD_OVER_PERIOD_MULTIPLIER}x prior period)",
               int(joined["T12_FLOODING_FLAG"].sum()), len(tx))
    report.row(f"T13: dormant reactivation (<= {DORMANT_MAX_TXN_COUNT} baseline txns, "
               f">= USD {REACTIVATION_MIN_AMOUNT_USD:,.0f} transaction)",
               int(joined["T13_REACTIVATION_FLAG"].sum()), len(tx))

    return joined[["TRANSACTION_ID", "T12_FLOODING_FLAG", "T13_DORMANT_BASELINE_FLAG", "T13_REACTIVATION_FLAG"]]


def build_transaction_features(report: FeatureReport, companies: pd.DataFrame,
                                as_of: pd.Timestamp) -> pd.DataFrame:
    tx = load("TRANSACTIONS")
    baselines = load("TRANSACTION_BASELINES")
    current_bl = select_baselines(baselines)

    report.section("TRANSACTION-LEVEL TYPOLOGY FEATURES")

    t01_flag = compute_t01_structuring(tx, report)
    z, t02_flag, t02_daily_flag = compute_t02_velocity(tx, current_bl, report)
    t03_flag = compute_t03_passthrough(tx, report)
    t04_flag = compute_t04_roundtrip(tx, report)
    t09 = compute_t09_sanctions(tx, report)
    t10_t11 = compute_t10_t11(tx, report)
    t12_t13 = compute_t12_t13(tx, current_bl, companies, report)

    features = tx[[
        "TRANSACTION_ID", "TRANSACTION_UUID", "ORIGINATOR_COMPANY_ID", "BENEFICIARY_COMPANY_ID",
        "AMOUNT_ORIGINAL", "CURRENCY_ORIGINAL", "AMOUNT_USD", "INITIATED_AT",
        "ORIGINATING_COUNTRY_ID", "DESTINATION_COUNTRY_ID", "BENEFICIARY_COUNTRY_ID",
    ]].copy()
    features["T01_STRUCTURING_FLAG"] = t01_flag.values
    features["T02_VELOCITY_ZSCORE"] = z
    features["T02_VELOCITY_FLAG"] = t02_flag
    features["T02_DAILY_COUNT_FLAG"] = t02_daily_flag
    features["T03_PASSTHROUGH_FLAG"] = t03_flag.values
    features["T04_ROUNDTRIP_FLAG"] = t04_flag.values
    features = features.merge(t09, on="TRANSACTION_ID", how="left")
    features = features.merge(t10_t11, on="TRANSACTION_ID", how="left")
    features = features.merge(t12_t13, on="TRANSACTION_ID", how="left")

    features["TRANSACTION_TYPOLOGY_POINTS"] = np.clip(
        features["T01_STRUCTURING_FLAG"].astype(int) * TYPOLOGY_POINTS["T01"]
        + features["T02_VELOCITY_FLAG"].fillna(False).astype(int) * TYPOLOGY_POINTS["T02"]
        + features["T03_PASSTHROUGH_FLAG"].astype(int) * TYPOLOGY_POINTS["T03"]
        + features["T04_ROUNDTRIP_FLAG"].astype(int) * TYPOLOGY_POINTS["T04"]
        + features["T09_SANCTIONS_FLAG"].fillna(False).astype(int) * TYPOLOGY_POINTS["T09"]
        + features["T10_JURISDICTION_HOP_FLAG"].fillna(False).astype(int) * TYPOLOGY_POINTS["T10"]
        + features["T11_HIGH_RISK_CORRIDOR_FLAG"].fillna(False).astype(int) * TYPOLOGY_POINTS["T11"]
        + features["T12_FLOODING_FLAG"].fillna(False).astype(int) * TYPOLOGY_POINTS["T12"]
        + features["T13_REACTIVATION_FLAG"].fillna(False).astype(int) * TYPOLOGY_POINTS["T13"],
        0, 100,
    )

    # Attach originator's company-level typology points/flags for the final combined score.
    company_cols = [
        "COMPANY_ID", "COMPANY_TYPOLOGY_POINTS", "T05_SHELL_FLAG", "T06_NOMINEE_FLAG",
        "T07_OPACITY_FLAG", "T08_FLAG",
    ]
    features = features.merge(
        companies[company_cols].rename(columns={c: f"ORIGINATOR_{c}" for c in company_cols if c != "COMPANY_ID"}),
        left_on="ORIGINATOR_COMPANY_ID", right_on="COMPANY_ID", how="left",
    ).drop(columns="COMPANY_ID")

    features["TOTAL_TYPOLOGY_POINTS"] = np.clip(
        features["TRANSACTION_TYPOLOGY_POINTS"] + features["ORIGINATOR_COMPANY_TYPOLOGY_POINTS"].fillna(0),
        0, 100,
    )
    features["EXPOSURE_FACTOR"] = features["TOTAL_TYPOLOGY_POINTS"] * 0.20  # weight per topfraudandtables.json

    report.section("COMBINED EXPOSURE SCORE (TYPOLOGY_STRENGTH factor, weight 0.20, capped at 100)")
    report.row("transactions with any typology signal (TOTAL_TYPOLOGY_POINTS > 0)",
               int((features["TOTAL_TYPOLOGY_POINTS"] > 0).sum()), len(features))
    report.row("transactions at the 100-point cap", int((features["TOTAL_TYPOLOGY_POINTS"] >= 100).sum()),
               len(features))

    return features


def main():
    report = FeatureReport()
    tx_preview = load("TRANSACTIONS")
    as_of = tx_preview["INITIATED_AT"].max()
    report.section(f"SightLine Fraud Feature Report - as-of date: {as_of}")

    companies = build_company_features(report, as_of)
    tx_features = build_transaction_features(report, companies, as_of)

    companies.to_csv(os.path.join(DATA_DIR, "COMPANY_FRAUD_FEATURES.csv"), index=False)
    companies.to_parquet(os.path.join(DATA_DIR, "COMPANY_FRAUD_FEATURES.parquet"), index=False)
    tx_features.to_csv(os.path.join(DATA_DIR, "TRANSACTION_FRAUD_FEATURES.csv"), index=False)
    tx_features.to_parquet(os.path.join(DATA_DIR, "TRANSACTION_FRAUD_FEATURES.parquet"), index=False)

    report.section("OUTPUT")
    report.row("COMPANY_FRAUD_FEATURES rows", len(companies))
    report.row("TRANSACTION_FRAUD_FEATURES rows", len(tx_features))
    report.note(f"written to {DATA_DIR} as .csv and .parquet - this is the context/feature table "
                 "for a model predict() call (e.g. RPT target_columns like OVERALL_RISK_SCORE / "
                 "IS_ANOMALY), not a prediction itself")

    report.dump()
    return companies, tx_features


if __name__ == "__main__":
    main()
