"""Fills each typology's narrative_template (topfraudandtables.json) with
real values from the row where the underlying columns make that
straightforward, falling back to a generic-but-still-grounded templated
string built from the typology's own published params otherwise (documented
per-typology below - mirrors the caveats already called out in
build_fraud_features.py / fraud_feature_report.txt).
"""
from __future__ import annotations

# Constants copied from build_fraud_features.py so narrative text matches the
# actual parameters the rule engine used to compute the flags (not
# arbitrary/independent numbers).
STRUCTURING_THRESHOLD_USD = 10_000
STRUCTURING_BAND_FLOOR_PCT = 0.90
STRUCTURING_MIN_COUNT = 3
STRUCTURING_WINDOW_HOURS = 72
PASSTHROUGH_DWELL_DAYS = 5
PASSTHROUGH_INFLOW_RETENTION_PCT = 0.85
ROUNDTRIP_CYCLE_WINDOW_DAYS = 30
ROUNDTRIP_AMOUNT_TOLERANCE_PCT = 0.15
NOMINEE_MIN_COMPANIES_PER_OWNER = 3
OWNERSHIP_STALE_VERIFICATION_MONTHS = 24
CORRUPTION_INDEX_THRESHOLD = 60
FATF_POINTS = {"BLACK_LIST": 40, "NON_COMPLIANT": 40, "GREY_LIST": 15, "MEMBER": 0}
SANCTIONED_JURISDICTION_POINTS = 40


def _fmt_money(v) -> str:
    if v is None:
        return "an undisclosed amount"
    try:
        return f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v)


def _country_name(countries_by_id: dict, country_id) -> str:
    if country_id is None:
        return "an unmapped jurisdiction"
    c = countries_by_id.get(int(country_id)) if country_id == country_id else None  # NaN guard
    return c["name"] if c else "an unmapped jurisdiction"


def build_narrative(typology_id: str, row: dict, countries_by_id: dict) -> str:
    """row: a dict of the merged per-transaction feature record (see
    data_store.py's ROW_COLUMNS for exactly what's available)."""

    if typology_id == "T01":
        # Real value: this transaction's own AMOUNT_ORIGINAL/CURRENCY_ORIGINAL.
        # `count`/window are the rule's own fixed parameters (per-originator
        # count isn't retained as its own output column by build_fraud_features.py,
        # only the resulting boolean flag is) - using the real per-typology
        # params here rather than fabricating a count.
        return (
            f"{STRUCTURING_MIN_COUNT}+ transfers per originator in the "
            f"{row['CURRENCY_ORIGINAL']} {STRUCTURING_THRESHOLD_USD * STRUCTURING_BAND_FLOOR_PCT:,.0f}-"
            f"{STRUCTURING_THRESHOLD_USD:,.0f} band (this transaction: "
            f"{row['CURRENCY_ORIGINAL']} {_fmt_money(row['AMOUNT_ORIGINAL'])}) against a "
            f"{row['CURRENCY_ORIGINAL']} {STRUCTURING_THRESHOLD_USD:,.0f} reporting threshold "
            f"within {STRUCTURING_WINDOW_HOURS} hours."
        )

    if typology_id == "T02":
        z = row.get("T02_VELOCITY_ZSCORE")
        z_txt = f"{z:.1f}" if z is not None and z == z else "an elevated number of"
        baseline_avg = row.get("BASELINE_AVG_AMOUNT_USD")
        period = row.get("BASELINE_PERIOD_LABEL") or "recent"
        if baseline_avg is not None and baseline_avg == baseline_avg:
            baseline_txt = f"USD {_fmt_money(baseline_avg)}"
        else:
            baseline_txt = "an unavailable (null/zero-stddev) baseline"
        return (
            f"Transaction of USD {_fmt_money(row['AMOUNT_USD'])} is {z_txt} standard deviations "
            f"above this entity's {period} baseline average of {baseline_txt}."
        )

    if typology_id == "T03":
        # Real value: this leg's own outbound AMOUNT_USD. build_fraud_features.py
        # doesn't retain the matched trailing inbound sum as an output column
        # (only the boolean flag), so the inbound figure uses the rule's own
        # published retention parameter rather than a fabricated number.
        return (
            f"Entity disbursed USD {_fmt_money(row['AMOUNT_USD'])} within a "
            f"{PASSTHROUGH_DWELL_DAYS}-day window, retaining at least "
            f"{PASSTHROUGH_INFLOW_RETENTION_PCT:.0%} of trailing inbound funds - "
            f"consistent with pass-through/funnel-account behaviour."
        )

    if typology_id == "T04":
        return (
            f"Funds traced through a 2-hop round-trip (A->B->A) returning to origin within "
            f"{ROUNDTRIP_CYCLE_WINDOW_DAYS} days, within {ROUNDTRIP_AMOUNT_TOLERANCE_PCT:.0%} "
            f"amount variance (transaction leg: USD {_fmt_money(row['AMOUNT_USD'])})."
        )

    if typology_id == "T05":
        revenue = row.get("ORIGINATOR_ANNUAL_REVENUE_USD")
        employees = row.get("ORIGINATOR_EMPLOYEE_COUNT")
        inc_country = _country_name(countries_by_id, row.get("ORIGINATOR_INCORPORATION_COUNTRY_ID"))
        hq_country = _country_name(countries_by_id, row.get("ORIGINATOR_HEADQUARTERS_COUNTRY_ID"))
        return (
            f"Originator reports USD {_fmt_money(revenue)} annual revenue with "
            f"{int(employees) if employees == employees else 'an unreported number of'} employees, "
            f"incorporated in {inc_country} but headquartered in {hq_country}."
        )

    if typology_id == "T06":
        return (
            f"A beneficial owner of the originator appears across "
            f">= {NOMINEE_MIN_COMPANIES_PER_OWNER} otherwise-unrelated companies "
            f"(name-normalized match per COMPANY_BENEFICIAL_OWNERS_NORMALIZED)."
        )

    if typology_id == "T07":
        declared = row.get("ORIGINATOR_TOTAL_DECLARED_PCT")
        if declared is not None and declared == declared and declared < 100:
            gap = 100 - declared
            return (
                f"Declared beneficial ownership for the originator totals {declared:.0f}%, "
                f"leaving {gap:.0f}% unaccounted for."
            )
        return (
            f"Beneficial-ownership verification for the originator is stale or missing "
            f"(>= {OWNERSHIP_STALE_VERIFICATION_MONTHS} months since last verification)."
        )

    if typology_id == "T08":
        return (
            f"A beneficial owner in the originator's ownership chain is a politically exposed "
            f"person resident in a jurisdiction with corruption index > {CORRUPTION_INDEX_THRESHOLD}, "
            f"or the originator itself is flagged PEP-associated with a high-corruption headquarters "
            f"jurisdiction."
        )

    if typology_id == "T09":
        beneficiary = row.get("BENEFICIARY_NAME") or "the beneficiary"
        entity = row.get("T09_SANCTIONS_MATCH_ENTITY") or "a sanctioned entity"
        score = row.get("T09_SANCTIONS_MATCH_SCORE")
        score_txt = f"{score:.0f}" if score is not None and score == score else "a high"
        return (
            f"Beneficiary '{beneficiary}' matches sanctioned entity '{entity}' at {score_txt}% "
            f"name-similarity (ENTITY_NAME fuzzy match; ALIASES field is unpopulated in this "
            f"environment per build_fraud_features.py)."
        )

    if typology_id == "T10":
        dest = _country_name(countries_by_id, row.get("DESTINATION_COUNTRY_ID"))
        bene = _country_name(countries_by_id, row.get("BENEFICIARY_COUNTRY_ID"))
        bic_country = row.get("BIC_COUNTRY_CODE") or "an unmapped"
        return (
            f"Payment routed to {dest} with beneficiary registered in {bene}, but settlement "
            f"bank BIC implies country '{bic_country}' - jurisdiction divergence on the "
            f"settlement leg."
        )

    if typology_id == "T11":
        # Identify which leg (originating/destination/beneficiary) actually
        # produced the stored T11_CORRIDOR_POINTS by recomputing the same
        # per-leg FATF/sanctions point lookup build_fraud_features.py used.
        corridor_points = row.get("T11_CORRIDOR_POINTS") or 0
        legs = [
            ("originating", row.get("ORIGINATING_COUNTRY_ID")),
            ("destination", row.get("DESTINATION_COUNTRY_ID")),
            ("beneficiary", row.get("BENEFICIARY_COUNTRY_ID")),
        ]
        for label, cid in legs:
            if cid is None or cid != cid:
                continue
            c = countries_by_id.get(int(cid))
            if not c:
                continue
            pts = FATF_POINTS.get(c["fatf_status"], 0) + (
                SANCTIONED_JURISDICTION_POINTS if c["sanctioned"] else 0
            )
            if pts == corridor_points and pts > 0:
                return (
                    f"Transaction's {label} jurisdiction is {c['name']}, currently "
                    f"{c['fatf_status']} (corruption index {c['corruption_index']:.0f})."
                )
        return (
            f"Transaction involves a jurisdiction on the FATF grey/black list or a sanctioned "
            f"country (corridor risk points: {corridor_points})."
        )

    if typology_id == "T12":
        return (
            f"This originator's new-counterparty rate this period materially exceeds its prior "
            f"period rate (per topfraudandtables.json T12 params: > 40% and >= 2.0x prior period)."
        )

    if typology_id == "T13":
        amount = row.get("AMOUNT_USD")
        return (
            f"Entity with a long-dormant baseline (<= 2 transactions in the trailing baseline "
            f"period) recorded a sudden USD {_fmt_money(amount)} transfer, consistent with "
            f"dormant-account reactivation."
        )

    return "Typology triggered; see topfraudandtables.json for detection logic."
