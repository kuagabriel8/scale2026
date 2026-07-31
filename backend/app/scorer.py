"""ML/RPT scoring layer abstraction.

`BaseScorer.score()` produces the `risk` + `explanation` halves of
risk_assessment_schema.json. Two implementations:

  - `DummyScorer`: fabricates deterministic-but-varied values that
    correlate with the deterministic rule layer (TOTAL_TYPOLOGY_POINTS)
    rather than being pure random, so the two halves of a triage row tell a
    plausible, internally-consistent story. Used when SCORER=dummy (see
    app/config.py) and as RPTScorer's internal fallback on any failure.
  - `RPTScorer`: the real SAP-RPT integration (sap-rpt-1.5-large tabular
    foundation model, in-context regression/classification, no gradient
    training - see rpt_ml_training_cycle.txt). Active by default
    (SCORER=rpt). See its class docstring below for the full design,
    including the proxy-label caveat for its few-shot context pool.

app/main.py's lifespan picks which one to instantiate based on
settings.SCORER - no other caller needs to change; every caller depends
only on BaseScorer.score().
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx
import pandas as pd

from . import scoring_math
from .config import settings

logger = logging.getLogger("sightline.scorer")

# Category -> typology ids, used to build the ML layer's fabricated
# component_scores sub-scores from the rule layer's category point totals.
CATEGORY_COMPONENT_MAP = {
    "amount_risk_score": ["T01"],
    "frequency_risk_score": ["T02", "T12"],
    "geography_risk_score": ["T10", "T11"],
    "counterparty_risk_score": ["T05", "T06", "T07", "T08"],
    "pattern_risk_score": ["T03", "T04"],
    "velocity_risk_score": ["T02", "T13"],
}

_ANOMALY_TYPE_BY_TYPOLOGY = {
    "T09": "SANCTIONS_ALIAS_MATCH",
    "T02": "VELOCITY_SPIKE",
    "T13": "DORMANT_REACTIVATION_SPIKE",
    "T04": "CIRCULAR_FLOW_PATTERN",
    "T10": "JURISDICTION_DIVERGENCE",
    "T11": "HIGH_RISK_CORRIDOR",
}

_CONTEXT_OUTCOMES = ["SAR_FILED", "TRUE_POSITIVE", "FALSE_POSITIVE", "UNRESOLVED"]


def _infer_anomaly_type(triggered: list[dict[str, Any]]) -> str:
    """Shared anomaly_type heuristic used by both DummyScorer and RPTScorer:
    convergence of >=3 typologies wins outright, else the first triggered
    typology with a mapped anomaly type, else a generic outlier label. Kept
    as one function so RPT's real is_anomaly prediction still gets a
    typology-grounded label rather than free-text from the model (RPT is
    tabular/non-generative and must not originate this text per
    topfraudandtables.json's governance.llm_role)."""
    if len(triggered) >= 3:
        return "MULTI_TYPOLOGY_CONVERGENCE"
    for sig in triggered:
        if sig["id"] in _ANOMALY_TYPE_BY_TYPOLOGY:
            return _ANOMALY_TYPE_BY_TYPOLOGY[sig["id"]]
    return "STATISTICAL_OUTLIER"


@dataclass
class ScoringContext:
    """Everything DummyScorer (or a future RPTScorer) needs to produce the
    ML-layer half of one transaction's assessment. Deliberately built from
    the *already-computed* deterministic layer (typology_signals, points) -
    per topfraudandtables.json's governance clause, the ML/LLM layer must be
    grounded in, not independent of, the rule layer's computed values."""

    transaction_id: int
    total_typology_points: int
    exposure_factor: float
    typology_signals: list[dict[str, Any]]
    update_count: int = 0


class BaseScorer(ABC):
    """Interface for the ML/RPT scoring layer. A real implementation is a
    drop-in replacement - callers only ever depend on this method."""

    @abstractmethod
    def score(self, ctx: ScoringContext) -> dict[str, Any]:
        """Returns {"risk": {...}, "explanation": {...}} matching the
        corresponding halves of risk_assessment_schema.json."""
        raise NotImplementedError


class DummyScorer(BaseScorer):
    """Fabricated-but-plausible scorer: overall_risk_score/model_confidence/
    component_scores/anomaly detection are all deterministic functions of
    (transaction_id, TOTAL_TYPOLOGY_POINTS, update_count) via scoring_math's
    seeded-noise helpers, so repeated calls (e.g. GET then a later re-render)
    return identical numbers unless update_count changes (simulated
    re-score), and results always correlate with the rule layer rather than
    looking random."""

    def score(self, ctx: ScoringContext) -> dict[str, Any]:
        triggered = [s for s in ctx.typology_signals if s["triggered"]]

        overall = float(
            scoring_math.overall_risk_score(ctx.total_typology_points, ctx.transaction_id, ctx.update_count)
        )
        confidence = float(
            scoring_math.model_confidence(ctx.total_typology_points, ctx.transaction_id, ctx.update_count)
        )
        tier = str(scoring_math.risk_tier(overall))

        is_anomaly = overall >= 70.0 or len(triggered) >= 3
        anomaly_type = _infer_anomaly_type(triggered) if is_anomaly else None

        points_by_id = {s["id"]: s["points"] for s in ctx.typology_signals}
        component_scores = {}
        for comp_name, typ_ids in CATEGORY_COMPONENT_MAP.items():
            base_points = sum(points_by_id.get(t, 0) for t in typ_ids)
            salt = (hash(comp_name) % 97) + 1.0
            component_scores[comp_name] = round(
                float(scoring_math.component_score(base_points, ctx.transaction_id, ctx.update_count, salt)), 1
            )

        risk = {
            "overall_risk_score": round(overall, 1),
            "risk_tier": tier,
            "model_confidence": round(confidence, 3),
            "is_anomaly": bool(is_anomaly),
            "anomaly_type": anomaly_type,
            "component_scores": component_scores,
        }

        explanation = self._build_explanation(ctx, risk, triggered)
        return {"risk": risk, "explanation": explanation}

    def _build_explanation(
        self, ctx: ScoringContext, risk: dict[str, Any], triggered: list[dict[str, Any]]
    ) -> dict[str, Any]:
        total_points = sum(s["points"] for s in triggered) or 1
        top_column_scores = sorted(
            (
                {
                    "column": f"{s['id']}_{_SOURCE_COLUMN_SUFFIX.get(s['id'], 'FLAG')}",
                    "contribution_score": round(s["points"] / total_points, 3),
                }
                for s in triggered
            ),
            key=lambda c: c["contribution_score"],
            reverse=True,
        )[:4]

        top_relevant_context_rows = self._build_context_rows(ctx, risk, triggered)

        if triggered:
            names = ", ".join(f"{s['id']} ({s['name']})" for s in triggered[:4])
            narrative_text = (
                f"Flagged for {names}; combined typology strength reached "
                f"{ctx.total_typology_points} points (exposure factor {ctx.exposure_factor}). "
                f"ML layer estimates overall risk {risk['overall_risk_score']} "
                f"({risk['risk_tier']}, confidence {risk['model_confidence']})."
            )
        else:
            narrative_text = (
                f"No deterministic typology signals triggered. ML layer estimates overall risk "
                f"{risk['overall_risk_score']} ({risk['risk_tier']}, confidence "
                f"{risk['model_confidence']})."
            )

        return {
            "top_column_scores": top_column_scores,
            "top_relevant_context_rows": top_relevant_context_rows,
            "narrative_text": narrative_text,
        }

    def _build_context_rows(self, ctx: ScoringContext, risk: dict, triggered: list[dict]) -> list[dict]:
        if not triggered:
            return []
        rows = []
        n = min(3, max(1, len(triggered)))
        for i in range(n):
            digest = hashlib.sha256(f"{ctx.transaction_id}:{i}:{ctx.update_count}".encode()).hexdigest()
            year = 2024 + (int(digest[:4], 16) % 3)
            case_num = int(digest[4:9], 16) % 99999
            is_case = int(digest[9], 16) % 2 == 0
            reference_id = f"{'CASE' if is_case else 'ALERT'}-{year}-{case_num:05d}"
            similarity = 0.60 + (risk["overall_risk_score"] / 100.0) * 0.30 + (int(digest[10:12], 16) / 255.0) * 0.08
            similarity = min(0.99, round(similarity, 2))
            outcome_idx = int(digest[12:14], 16) % len(_CONTEXT_OUTCOMES)
            # Skew toward resolved/positive outcomes for higher-risk rows -
            # a mocked stand-in for RPT's `top_relevant_context_rows`.
            if risk["overall_risk_score"] >= 70 and outcome_idx > 1:
                outcome_idx = int(digest[12:14], 16) % 2
            rows.append(
                {
                    "reference_id": reference_id,
                    "similarity_score": similarity,
                    "outcome": _CONTEXT_OUTCOMES[outcome_idx],
                }
            )
        return rows


_SOURCE_COLUMN_SUFFIX = {
    "T01": "STRUCTURING_FLAG",
    "T02": "VELOCITY_ZSCORE",
    "T03": "PASSTHROUGH_FLAG",
    "T04": "ROUNDTRIP_FLAG",
    "T05": "SHELL_FLAG",
    "T06": "NOMINEE_FLAG",
    "T07": "OPACITY_FLAG",
    "T08": "PEP_FLAG",
    "T09": "SANCTIONS_MATCH_SCORE",
    "T10": "JURISDICTION_HOP_FLAG",
    "T11": "CORRIDOR_POINTS",
    "T12": "FLOODING_FLAG",
    "T13": "REACTIVATION_FLAG",
}


# T01-T13 -> the numeric flag column name sent to RPT in `data_schema`/`rows`.
# Mirrors data_store.TRIGGER_COLUMN's typology set but scorer.py can't import
# data_store (data_store imports this module), so this is a small,
# independently-maintained id list - it only needs the ordered T01..T13 ids,
# not the underlying dataframe column names, since ScoringContext.typology_signals
# already carries `id`/`triggered`/`points` per typology.
_TYPOLOGY_IDS = [f"T{n:02d}" for n in range(1, 14)]

# Categories from topfraudandtables.json, used only to stratify the context
# pool sample (see _build_context_pool) across a spread of typology kinds.
_CATEGORIES = [
    "placement", "layering", "structure", "geography",
    "behavioural_deviation", "sanctions", "predicate_offence", "NONE",
]


class RPTScorer(BaseScorer):
    """Real SAP-RPT integration: SAP-RPT (sap-rpt-1.5-large) is a pretrained
    tabular foundation model that does in-context regression/classification
    over a batch of rows in a single forward pass - no gradient training, no
    persisted model artifact (see rpt_ml_training_cycle.txt section 1). Each
    `score()` call:

      1. Gets a cached OAuth client-credentials token (AICORE_AUTH_URL),
         refreshed with a safety buffer before it expires.
      2. Builds a payload from a small, stratified, PROXY-LABELED context
         pool (see `_build_context_pool`) plus one target row built from
         `ctx` with OVERALL_RISK_SCORE/IS_ANOMALY masked as "?".
      3. POSTs to f"{RPT_URL}/predict" and maps the response onto the
         risk_assessment_schema.json `risk`/`explanation` shape.
      4. On ANY failure (network, timeout, unexpected response shape),
         logs a warning and falls back to an internal DummyScorer instance
         so a live demo never 500s because the RPT endpoint hiccups.

    PROXY LABELS - IMPORTANT CAVEAT: there is no real historical
    COMPLIANCE_CASES/RISK_ALERTS outcome data in this environment (verified:
    cleaned_data/ has no such tables). The context pool's OVERALL_RISK_SCORE/
    IS_ANOMALY "labels" are NOT ground truth - they are computed with the
    exact same deterministic heuristic as DummyScorer/scoring_math
    (overall_risk_score(), risk_tier(), and the same is_anomaly rule), used
    ONLY to give RPT few-shot context to learn a smoother, generalizing
    regression from. The point of routing through RPT instead of keeping
    DummyScorer is (a) RPT's few-shot in-context regression/classification
    generalizes across rows instead of DummyScorer's per-row sin/hash mock,
    and (b) RPT returns a real model-native confidence signal (a regression
    confidence_interval and a classification confidence) instead of a
    fabricated one. Never treat context-pool rows as real resolved
    cases/alerts beyond this narrow few-shot-example role.
    """

    # Class-level so the (small, ~200-row) context pool and the OAuth token
    # are shared across RPTScorer instances/requests within a process
    # instead of being rebuilt or re-fetched per instance.
    _context_pool_lock = threading.Lock()
    _context_pool_cache: list[dict[str, Any]] | None = None

    def __init__(self) -> None:
        self._fallback = DummyScorer()
        self._token_lock = threading.Lock()
        self._token: str | None = None
        self._token_expiry_monotonic: float = 0.0
        self._result_cache_lock = threading.Lock()
        self._result_cache: dict[tuple[int, int], dict[str, Any]] = {}
        self._client = httpx.Client(timeout=settings.RPT_TIMEOUT_SECONDS)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------
    def score(self, ctx: ScoringContext) -> dict[str, Any]:
        cache_key = (ctx.transaction_id, ctx.update_count)
        with self._result_cache_lock:
            cached = self._result_cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            result = self._score_via_rpt(ctx)
        except Exception as exc:  # noqa: BLE001 - any failure must fall back, never 500
            logger.warning(
                "RPTScorer: predict call failed for transaction_id=%s (update_count=%s); "
                "falling back to DummyScorer. Error: %s",
                ctx.transaction_id, ctx.update_count, exc,
            )
            result = self._fallback.score(ctx)

        with self._result_cache_lock:
            self._result_cache[cache_key] = result
        return result

    # ------------------------------------------------------------------
    # OAuth token (cached, refreshed with a safety buffer before expiry)
    # ------------------------------------------------------------------
    def _get_token(self) -> str:
        with self._token_lock:
            if self._token is not None and time.monotonic() < self._token_expiry_monotonic:
                return self._token

            resp = httpx.post(
                f"{settings.AICORE_AUTH_URL}/oauth/token",
                params={"grant_type": "client_credentials"},
                auth=(settings.AICORE_CLIENT_ID, settings.AICORE_CLIENT_SECRET),
                timeout=settings.RPT_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            payload = resp.json()
            token = payload["access_token"]
            expires_in = float(payload.get("expires_in", 3600))
            self._token = token
            self._token_expiry_monotonic = (
                time.monotonic() + max(0.0, expires_in - settings.RPT_TOKEN_REFRESH_BUFFER_SECONDS)
            )
            return token

    # ------------------------------------------------------------------
    # Context pool (proxy-labeled few-shot examples, built once per process)
    # ------------------------------------------------------------------
    def _get_context_pool(self) -> list[dict[str, Any]]:
        if RPTScorer._context_pool_cache is not None:
            return RPTScorer._context_pool_cache
        with RPTScorer._context_pool_lock:
            if RPTScorer._context_pool_cache is None:
                RPTScorer._context_pool_cache = self._build_context_pool()
        return RPTScorer._context_pool_cache

    def _build_context_pool(self) -> list[dict[str, Any]]:
        """Samples a modest (~RPT_CONTEXT_POOL_SIZE row), stratified,
        PROXY-labeled pool from TRANSACTION_FRAUD_FEATURES.parquet's
        SPLIT == 'train' rows (assigned chronologically by
        build_fraud_features.py's assign_time_split - see
        rpt_ml_training_cycle.txt section 4). Stratifies by each row's
        "primary" typology category (the category of its highest-point
        triggered typology, or "NONE") so the few-shot context spans a
        spread of typology kinds rather than being dominated by whichever
        category is most common in the raw data.

        PROXY LABELS: OVERALL_RISK_SCORE/IS_ANOMALY for these rows are
        computed with the same deterministic heuristic as DummyScorer/
        scoring_math - there is no real resolved-case ground truth in this
        environment. See the RPTScorer class docstring for the full caveat.
        """
        from .typologies import load_typology_defs  # local import: avoid unused import when RPT never runs

        typology_defs = load_typology_defs()
        points_by_typology = {tid: typology_defs[tid].typology_points for tid in _TYPOLOGY_IDS if tid in typology_defs}
        category_by_typology = {tid: typology_defs[tid].category for tid in _TYPOLOGY_IDS if tid in typology_defs}

        needed_cols = [
            "TRANSACTION_ID", "TOTAL_TYPOLOGY_POINTS", "EXPOSURE_FACTOR", "SPLIT",
            "T01_STRUCTURING_FLAG", "T02_VELOCITY_FLAG", "T03_PASSTHROUGH_FLAG", "T04_ROUNDTRIP_FLAG",
            "ORIGINATOR_T05_SHELL_FLAG", "ORIGINATOR_T06_NOMINEE_FLAG", "ORIGINATOR_T07_OPACITY_FLAG",
            "ORIGINATOR_T08_FLAG", "T09_SANCTIONS_FLAG", "T10_JURISDICTION_HOP_FLAG",
            "T11_HIGH_RISK_CORRIDOR_FLAG", "T12_FLOODING_FLAG", "T13_REACTIVATION_FLAG",
        ]
        flag_col_by_typology = dict(zip(_TYPOLOGY_IDS, needed_cols[4:]))

        df = pd.read_parquet(settings.TRANSACTION_FEATURES_PATH, columns=needed_cols)
        train = df[df["SPLIT"] == "train"]
        if train.empty:
            train = df  # defensive fallback if SPLIT is missing/empty in some environment

        flags = {tid: train[col].fillna(False).to_numpy(dtype=bool) for tid, col in flag_col_by_typology.items()}

        def _primary_category(idx: int) -> str:
            best_cat, best_points = "NONE", -1
            for tid in _TYPOLOGY_IDS:
                if flags[tid][idx] and points_by_typology.get(tid, 0) > best_points:
                    best_cat = category_by_typology[tid]
                    best_points = points_by_typology[tid]
            return best_cat

        n = len(train)
        primary_categories = [_primary_category(i) for i in range(n)]
        train = train.assign(_PRIMARY_CATEGORY=primary_categories)

        per_bucket = max(1, settings.RPT_CONTEXT_POOL_SIZE // len(_CATEGORIES))
        sampled_frames = []
        for cat in _CATEGORIES:
            bucket = train[train["_PRIMARY_CATEGORY"] == cat]
            if bucket.empty:
                continue
            take = min(per_bucket, len(bucket))
            sampled_frames.append(bucket.sample(n=take, random_state=42))
        if not sampled_frames:
            return []
        pool_df = pd.concat(sampled_frames, ignore_index=True)

        pool: list[dict[str, Any]] = []
        for _, row in pool_df.iterrows():
            total_points = int(row["TOTAL_TYPOLOGY_POINTS"])
            txn_id = int(row["TRANSACTION_ID"])
            triggered_count = sum(1 for tid in _TYPOLOGY_IDS if bool(row[flag_col_by_typology[tid]]))
            overall = float(scoring_math.overall_risk_score(total_points, txn_id, update_count=0))
            is_anomaly = overall >= 70.0 or triggered_count >= 3

            entry: dict[str, Any] = {"transaction_id": txn_id}
            for tid in _TYPOLOGY_IDS:
                entry[f"{tid}_FLAG"] = int(bool(row[flag_col_by_typology[tid]]))
            entry["TOTAL_TYPOLOGY_POINTS"] = total_points
            entry["EXPOSURE_FACTOR"] = float(row["EXPOSURE_FACTOR"])
            entry["OVERALL_RISK_SCORE"] = round(overall, 1)
            entry["IS_ANOMALY"] = "1" if is_anomaly else "0"
            pool.append(entry)
        return pool

    # ------------------------------------------------------------------
    # Predict call + response mapping
    # ------------------------------------------------------------------
    def _build_target_row(self, ctx: ScoringContext) -> dict[str, Any]:
        points_by_id = {s["id"]: s["points"] for s in ctx.typology_signals}
        triggered_by_id = {s["id"]: bool(s["triggered"]) for s in ctx.typology_signals}
        row: dict[str, Any] = {"transaction_id": ctx.transaction_id}
        for tid in _TYPOLOGY_IDS:
            row[f"{tid}_FLAG"] = int(bool(triggered_by_id.get(tid, False)))
        row["TOTAL_TYPOLOGY_POINTS"] = ctx.total_typology_points
        row["EXPOSURE_FACTOR"] = ctx.exposure_factor
        row["OVERALL_RISK_SCORE"] = "?"
        row["IS_ANOMALY"] = "?"
        return row

    def _build_payload(self, ctx: ScoringContext, context_pool: list[dict[str, Any]]) -> dict[str, Any]:
        data_schema: dict[str, dict[str, str]] = {"transaction_id": {"dtype": "numeric"}}
        for tid in _TYPOLOGY_IDS:
            data_schema[f"{tid}_FLAG"] = {"dtype": "numeric"}
        data_schema["TOTAL_TYPOLOGY_POINTS"] = {"dtype": "numeric"}
        data_schema["EXPOSURE_FACTOR"] = {"dtype": "numeric"}
        data_schema["OVERALL_RISK_SCORE"] = {"dtype": "numeric"}
        data_schema["IS_ANOMALY"] = {"dtype": "string"}

        rows = list(context_pool) + [self._build_target_row(ctx)]

        return {
            "index_column": "transaction_id",
            "prediction_config": {
                "target_columns": [
                    {"name": "OVERALL_RISK_SCORE", "prediction_placeholder": "?", "task_type": "regression"},
                    {"name": "IS_ANOMALY", "prediction_placeholder": "?", "task_type": "classification", "top_k": 2},
                ],
                "explanations": {"top_column_scores": 4, "top_relevant_context_rows": 3},
            },
            "parse_data_types": "true",
            "data_schema": data_schema,
            "rows": rows,
        }

    def _predict(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._get_token()}",
            "AI-Resource-Group": settings.AICORE_RESOURCE_GROUP,
        }
        url = f"{settings.RPT_URL}/predict"
        last_exc: Exception | None = None
        for attempt in range(2):  # one retry on transient failure
            try:
                resp = self._client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                return resp.json()
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                logger.warning("RPTScorer: predict attempt %d failed transiently: %s", attempt + 1, exc)
                continue
        raise last_exc  # type: ignore[misc]

    def _score_via_rpt(self, ctx: ScoringContext) -> dict[str, Any]:
        context_pool = self._get_context_pool()
        payload = self._build_payload(ctx, context_pool)
        response = self._predict(payload)

        predictions = response["predictions"][0]
        overall_pred = predictions["OVERALL_RISK_SCORE"][0]
        overall = float(overall_pred["prediction"])
        overall = max(0.0, min(100.0, overall))

        anomaly_candidates = sorted(
            predictions["IS_ANOMALY"], key=lambda c: (c.get("confidence") or 0.0), reverse=True
        )
        top_anomaly = anomaly_candidates[0] if anomaly_candidates else {"prediction": "0", "confidence": 0.5}
        is_anomaly = str(top_anomaly["prediction"]) == "1"
        anomaly_confidence = top_anomaly.get("confidence")

        # model_confidence: prefer RPT's own classification confidence for
        # IS_ANOMALY (a real model-native signal); fall back to a heuristic
        # derived from the regression confidence_interval width (narrower
        # interval around the point estimate => higher confidence) since
        # this deployment returns confidence=null for regression targets.
        if anomaly_confidence is not None:
            confidence = float(anomaly_confidence)
        else:
            interval = overall_pred.get("confidence_interval")
            if interval and len(interval) == 2:
                width = max(0.0, float(interval[1]) - float(interval[0]))
                confidence = max(0.05, min(0.99, 1.0 - width / 100.0))
            else:
                confidence = 0.5

        tier = str(scoring_math.risk_tier(overall))
        triggered = [s for s in ctx.typology_signals if s["triggered"]]
        anomaly_type = _infer_anomaly_type(triggered) if is_anomaly else None

        points_by_id = {s["id"]: s["points"] for s in ctx.typology_signals}
        component_scores: dict[str, float] = {}
        for comp_name, typ_ids in CATEGORY_COMPONENT_MAP.items():
            base_points = sum(points_by_id.get(t, 0) for t in typ_ids)
            weight = base_points / ctx.total_typology_points if ctx.total_typology_points > 0 else 0.0
            component = overall * (0.4 + 0.6 * weight)
            component_scores[comp_name] = round(max(0.0, min(100.0, component)), 1)

        risk = {
            "overall_risk_score": round(overall, 1),
            "risk_tier": tier,
            "model_confidence": round(confidence, 3),
            "is_anomaly": bool(is_anomaly),
            "anomaly_type": anomaly_type,
            "component_scores": component_scores,
        }

        explanation = self._build_explanation(response, ctx, risk, triggered, context_pool)
        return {"risk": risk, "explanation": explanation}

    def _build_explanation(
        self,
        response: dict[str, Any],
        ctx: ScoringContext,
        risk: dict[str, Any],
        triggered: list[dict[str, Any]],
        context_pool: list[dict[str, Any]],
    ) -> dict[str, Any]:
        explanations = response.get("explanations", {})

        raw_column_scores = explanations.get("top_column_scores") or [{}]
        column_scores_map = raw_column_scores[0] if raw_column_scores else {}
        top_column_scores = [
            {"column": col, "contribution_score": round(float(score), 3)}
            for col, score in sorted(column_scores_map.items(), key=lambda kv: kv[1], reverse=True)
        ][:4]

        raw_context_rows = explanations.get("top_relevant_context_rows") or [[]]
        context_row_indices = raw_context_rows[0] if raw_context_rows else []
        top_relevant_context_rows = []
        for rank, idx in enumerate(context_row_indices[:3]):
            if not isinstance(idx, int) or not (0 <= idx < len(context_pool)):
                continue
            context_txn_id = context_pool[idx]["transaction_id"]
            # RPT returns these ranked by relevance but this deployment does
            # not expose a numeric similarity score per context row - use a
            # rank-derived ordinal value (most-relevant first) rather than
            # fabricating a precise score.
            similarity_score = round(max(0.5, 0.90 - rank * 0.15), 2)
            top_relevant_context_rows.append(
                {
                    "reference_id": f"TXN-{context_txn_id}",
                    "similarity_score": similarity_score,
                    # No real resolved COMPLIANCE_CASES/RISK_ALERTS outcome
                    # exists for these context rows (they are other
                    # transactions, not resolved cases) - honestly null
                    # rather than fabricated.
                    "outcome": None,
                }
            )

        if triggered:
            names = ", ".join(f"{s['id']} ({s['name']})" for s in triggered[:4])
            narrative_text = (
                f"Flagged for {names}; combined typology strength reached "
                f"{ctx.total_typology_points} points (exposure factor {ctx.exposure_factor}). "
                f"SAP-RPT few-shot estimate: overall risk {risk['overall_risk_score']} "
                f"({risk['risk_tier']}, confidence {risk['model_confidence']})."
            )
        else:
            narrative_text = (
                f"No deterministic typology signals triggered. SAP-RPT few-shot estimate: "
                f"overall risk {risk['overall_risk_score']} ({risk['risk_tier']}, confidence "
                f"{risk['model_confidence']})."
            )

        return {
            "top_column_scores": top_column_scores,
            "top_relevant_context_rows": top_relevant_context_rows,
            "narrative_text": narrative_text,
        }
