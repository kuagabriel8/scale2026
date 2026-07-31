"""In-memory data layer: loads cleaned_data/*.parquet at startup, joins in
just enough context (countries, BIC country, current baseline period,
beneficiary name, originator company features) to build real-value
narratives, and exposes list/detail/review-update operations used by the
REST routers and the streaming simulator.

Deliberately keeps the expensive nested per-transaction build (typology
signals with narratives, ML explanation, governance state) lazy - only
built for the rows actually being returned (a page, a single detail, or the
"just arrived" row in the stream) - while sort/filter/pagination run
vectorized over the full 150k-row dataframe so the list endpoint stays fast.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from . import scoring_math
from .config import settings
from .scorer import BaseScorer, ScoringContext
from .schema_validation import validate_assessment
from .typologies import load_typology_defs, ordered_typology_ids
from .narratives import build_narrative

# typology id -> boolean column in the merged dataframe that represents
# "triggered" for that typology (see build_fraud_features.py's column names;
# T05-T08 are company-level flags attached via the ORIGINATOR).
TRIGGER_COLUMN = {
    "T01": "T01_STRUCTURING_FLAG",
    "T02": "T02_VELOCITY_FLAG",
    "T03": "T03_PASSTHROUGH_FLAG",
    "T04": "T04_ROUNDTRIP_FLAG",
    "T05": "ORIGINATOR_T05_SHELL_FLAG",
    "T06": "ORIGINATOR_T06_NOMINEE_FLAG",
    "T07": "ORIGINATOR_T07_OPACITY_FLAG",
    "T08": "ORIGINATOR_T08_FLAG",
    "T09": "T09_SANCTIONS_FLAG",
    "T10": "T10_JURISDICTION_HOP_FLAG",
    "T11": "T11_HIGH_RISK_CORRIDOR_FLAG",
    "T12": "T12_FLOODING_FLAG",
    "T13": "T13_REACTIVATION_FLAG",
}

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class DataStore:
    def __init__(self, scorers: dict[str, BaseScorer], default_scorer: str):
        """`scorers`: every available ML/RPT scoring layer implementation,
        keyed by the name a caller passes as `?model=`/`{"model": ...}`
        (e.g. "rpt", "sklearn", "dummy" - see app/main.py's lifespan).
        `default_scorer`: which key to use when a caller doesn't pass an
        explicit choice (from settings.SCORER) - this is also always what
        the shared background stream simulator uses via bump_rescore(),
        since a broadcast stream has no per-viewer model concept."""
        self.scorers = scorers
        if default_scorer not in scorers:
            raise ValueError(
                f"DataStore: default_scorer {default_scorer!r} not in available scorers {sorted(scorers)}"
            )
        self.default_scorer_name = default_scorer
        self.typology_defs = load_typology_defs()
        self.typology_ids = ordered_typology_ids()

        self._lock = threading.RLock()
        self._load()

        # Mutable per-transaction state.
        n = len(self.df)
        self._review_status = np.full(n, "PENDING", dtype=object)
        self._reviewed_by: list[str | None] = [None] * n
        self._update_counts = np.zeros(n, dtype=np.int64)
        self._scored_at = np.full(n, self._loaded_at, dtype=object)

        # category -> boolean numpy array, precomputed once (static - the
        # deterministic rule layer never changes at runtime).
        self._category_flags: dict[str, np.ndarray] = {}
        categories = {t.id: t.category for t in self.typology_defs.values()}
        for tid, col in TRIGGER_COLUMN.items():
            cat = categories[tid]
            flag = self.df[col].fillna(False).to_numpy(dtype=bool)
            if cat in self._category_flags:
                self._category_flags[cat] |= flag
            else:
                self._category_flags[cat] = flag.copy()

        self._triggered_count = np.zeros(n, dtype=np.int64)
        for col in TRIGGER_COLUMN.values():
            self._triggered_count += self.df[col].fillna(False).to_numpy(dtype=bool).astype(np.int64)

        self._total_points = self.df["TOTAL_TYPOLOGY_POINTS"].to_numpy(dtype=np.float64)
        self._transaction_ids = self.df["TRANSACTION_ID"].to_numpy(dtype=np.int64)
        self._pos_by_id = {tid: i for i, tid in enumerate(self._transaction_ids)}

        # Streaming order: by INITIATED_AT ascending (simulated arrival order).
        self._stream_order_positions = np.argsort(
            self.df["INITIATED_AT"].to_numpy(), kind="stable"
        )

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def _load(self) -> None:
        nrows = settings.MAX_ROWS
        tx_features = pd.read_parquet(settings.TRANSACTION_FEATURES_PATH)
        if nrows:
            tx_features = tx_features.head(nrows).copy()

        transactions = pd.read_parquet(
            settings.TRANSACTIONS_PATH,
            columns=["TRANSACTION_ID", "BENEFICIARY_NAME"],
        )
        bic = pd.read_parquet(
            settings.CLEANED_DATA_DIR / "TRANSACTIONS_BIC_COUNTRY.parquet",
            columns=["TRANSACTION_ID", "BIC_COUNTRY_CODE"],
        )
        countries = pd.read_parquet(settings.COUNTRIES_PATH)
        company_features = pd.read_parquet(settings.COMPANY_FEATURES_PATH)
        baselines = pd.read_parquet(
            settings.BASELINES_PATH,
            columns=["COMPANY_ID", "BASELINE_PERIOD", "PERIOD_END", "AVG_AMOUNT_USD"],
        )

        # Most-recent baseline period per company (mirrors
        # build_fraud_features.py's select_baselines()), used only for T02's
        # narrative (real baseline average/period label).
        baselines = baselines.sort_values(["COMPANY_ID", "PERIOD_END"])
        current_baseline = baselines.groupby("COMPANY_ID", as_index=False).tail(1)
        current_baseline = current_baseline.rename(
            columns={"AVG_AMOUNT_USD": "BASELINE_AVG_AMOUNT_USD", "BASELINE_PERIOD": "BASELINE_PERIOD_LABEL"}
        )[["COMPANY_ID", "BASELINE_AVG_AMOUNT_USD", "BASELINE_PERIOD_LABEL"]]

        company_cols = company_features[
            [
                "COMPANY_ID", "EMPLOYEE_COUNT", "ANNUAL_REVENUE_USD",
                "INCORPORATION_COUNTRY_ID", "HEADQUARTERS_COUNTRY_ID",
                "TOTAL_DECLARED_PCT",
            ]
        ].rename(columns=lambda c: f"ORIGINATOR_{c}" if c != "COMPANY_ID" else c)

        df = tx_features.merge(transactions, on="TRANSACTION_ID", how="left")
        df = df.merge(bic, on="TRANSACTION_ID", how="left")
        df = df.merge(
            company_cols, left_on="ORIGINATOR_COMPANY_ID", right_on="COMPANY_ID", how="left"
        ).drop(columns="COMPANY_ID")
        df = df.merge(
            current_baseline, left_on="ORIGINATOR_COMPANY_ID", right_on="COMPANY_ID", how="left"
        ).drop(columns="COMPANY_ID")

        df = df.reset_index(drop=True)
        self.df = df

        self.countries_by_id: dict[int, dict[str, Any]] = {
            int(row["COUNTRY_ID"]): {
                "code": row["COUNTRY_CODE"],
                "name": row["COUNTRY_NAME"],
                "fatf_status": row["FATF_STATUS"],
                "corruption_index": float(row["CORRUPTION_INDEX"]),
                "sanctioned": bool(row["SANCTIONS_LIST"]),
            }
            for _, row in countries.iterrows()
        }

        self._loaded_at = _utcnow_iso()

    # ------------------------------------------------------------------
    # Vectorized scoreboard (sort/filter over the whole dataset)
    # ------------------------------------------------------------------
    def _scoreboard(self) -> pd.DataFrame:
        overall = scoring_math.overall_risk_score(
            self._total_points, self._transaction_ids, self._update_counts
        )
        tier = scoring_math.risk_tier(overall)
        severity = scoring_math.severity(overall, self._total_points)
        return pd.DataFrame(
            {
                "TRANSACTION_ID": self._transaction_ids,
                "overall_risk_score": overall,
                "risk_tier": tier,
                "typology_strength_points": self._total_points,
                "severity": severity,
                "review_status": self._review_status,
                "pos": np.arange(len(self._transaction_ids)),
            }
        )

    # ------------------------------------------------------------------
    # Scorer resolution
    # ------------------------------------------------------------------
    def resolve_scorer(self, model: str | None) -> BaseScorer:
        """Looks up a named scorer (e.g. "rpt"/"sklearn"/"dummy"), or the
        configured default when `model` is None/omitted. Raises KeyError on
        an unknown name - callers (routers) are expected to translate that
        into a clean 4xx rather than a 500."""
        name = model or self.default_scorer_name
        try:
            return self.scorers[name]
        except KeyError:
            raise KeyError(
                f"Unknown model {name!r}; available: {sorted(self.scorers)}"
            ) from None

    # ------------------------------------------------------------------
    # Public read API
    # ------------------------------------------------------------------
    def list_assessments(
        self,
        page: int = 1,
        page_size: int = 25,
        sort: str = "severity",
        order: str = "desc",
        risk_tier: str | None = None,
        category: str | None = None,
        review_status: str | None = None,
        model: str | None = None,
    ) -> tuple[list[dict], int]:
        scorer = self.resolve_scorer(model)
        board = self._scoreboard()

        if risk_tier:
            board = board[board["risk_tier"] == risk_tier.upper()]
        if review_status:
            board = board[board["review_status"] == review_status.upper()]
        if category:
            cat_flags = self._category_flags.get(category)
            if cat_flags is None:
                board = board.iloc[0:0]
            else:
                keep_positions = set(np.nonzero(cat_flags)[0].tolist())
                board = board[board["pos"].isin(keep_positions)]

        sort_col_map = {
            "severity": "severity",
            "overall_risk_score": "overall_risk_score",
            "typology_strength_points": "typology_strength_points",
            "transaction_id": "TRANSACTION_ID",
        }
        sort_col = sort_col_map.get(sort, "severity")
        ascending = order.lower() == "asc"
        board = board.sort_values(sort_col, ascending=ascending, kind="stable")

        total = len(board)
        start = (page - 1) * page_size
        end = start + page_size
        page_positions = board["pos"].to_numpy()[start:end]

        items = [self._build_assessment_at(pos, scorer) for pos in page_positions]
        return items, total

    def get_assessment(self, transaction_id: int, model: str | None = None) -> dict | None:
        pos = self._pos_by_id.get(transaction_id)
        if pos is None:
            return None
        scorer = self.resolve_scorer(model)
        return self._build_assessment_at(pos, scorer)

    def transaction_ids_in_stream_order(self) -> list[int]:
        return [int(self._transaction_ids[p]) for p in self._stream_order_positions]

    def bump_rescore(self, transaction_id: int) -> dict | None:
        """Simulates a re-score (e.g. a later transaction pushed this one
        into a structuring window): increments this transaction's
        update_count, which perturbs the deterministic-but-varied ML score
        via scoring_math, and refreshes scored_at. Always uses the default
        scorer (no per-viewer concept for the shared broadcast stream)."""
        pos = self._pos_by_id.get(transaction_id)
        if pos is None:
            return None
        with self._lock:
            self._update_counts[pos] += 1
            self._scored_at[pos] = _utcnow_iso()
        return self._build_assessment_at(pos, self.scorers[self.default_scorer_name])

    def update_review(self, transaction_id: int, review_status: str, reviewed_by: str | None) -> dict | None:
        pos = self._pos_by_id.get(transaction_id)
        if pos is None:
            return None
        with self._lock:
            self._review_status[pos] = review_status
            self._reviewed_by[pos] = reviewed_by
        return self._build_assessment_at(pos, self.scorers[self.default_scorer_name])

    # ------------------------------------------------------------------
    # Assessment construction (the expensive, lazily-built nested object)
    # ------------------------------------------------------------------
    def _build_typology_signals(self, row: dict) -> list[dict]:
        signals = []
        for tid in self.typology_ids:
            tdef = self.typology_defs[tid]
            col = TRIGGER_COLUMN[tid]
            triggered = bool(row.get(col) or False)
            points = tdef.typology_points if triggered else 0
            narrative = build_narrative(tid, row, self.countries_by_id) if triggered else None
            signals.append(
                {
                    "id": tid,
                    "name": tdef.name,
                    "category": tdef.category,
                    "triggered": triggered,
                    "points": points,
                    "narrative": narrative,
                }
            )
        return signals

    def _build_assessment_at(self, pos: int, scorer: BaseScorer) -> dict:
        row = self.df.iloc[int(pos)].to_dict()
        transaction_id = int(row["TRANSACTION_ID"])

        typology_signals = self._build_typology_signals(row)
        total_points = int(row["TOTAL_TYPOLOGY_POINTS"])
        exposure_factor = float(row["EXPOSURE_FACTOR"])
        exposure = {"typology_strength_points": total_points, "exposure_factor": exposure_factor}

        update_count = int(self._update_counts[pos])
        ctx = ScoringContext(
            transaction_id=transaction_id,
            total_typology_points=total_points,
            exposure_factor=exposure_factor,
            typology_signals=typology_signals,
            update_count=update_count,
        )
        scored = scorer.score(ctx)

        governance = {
            "requires_human_review": True,
            "review_status": self._review_status[pos],
            "reviewed_by": self._reviewed_by[pos],
        }

        # settings.MODEL_VERSION is an optional override for whichever
        # scorer is the process-wide default; every other named scorer
        # reports its own descriptive model_version (see BaseScorer
        # subclasses in app/scorer.py).
        model_version = scorer.model_version
        if settings.MODEL_VERSION and scorer is self.scorers.get(self.default_scorer_name):
            model_version = settings.MODEL_VERSION

        assessment = {
            "transaction_id": transaction_id,
            "transaction_uuid": row["TRANSACTION_UUID"],
            "scored_at": self._scored_at[pos],
            "model_version": model_version,
            "risk": scored["risk"],
            "typology_signals": typology_signals,
            "exposure": exposure,
            "explanation": scored["explanation"],
            "governance": governance,
        }

        if settings.VALIDATE_RESPONSES:
            validate_assessment(assessment)

        return assessment
