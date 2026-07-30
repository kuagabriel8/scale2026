"""ML/RPT scoring layer abstraction.

`BaseScorer.score()` produces the `risk` + `explanation` halves of
risk_assessment_schema.json. `DummyScorer` is the active implementation
until a real model is wired in - it fabricates deterministic-but-varied
values that correlate with the deterministic rule layer (TOTAL_TYPOLOGY_POINTS)
rather than being pure random, so the two halves of a triage row tell a
plausible, internally-consistent story.

To swap in the real model: implement `RPTScorer.score()` following the
OAuth client-credentials + POST .../predict pattern in rpt_predict_demo.py
(token via AICORE_AUTH_URL, headers with AI-Resource-Group, payload shaped
per `prediction_config.target_columns` / `explanations`), map the response's
target-column predictions onto `risk.*` and `explanations.top_column_scores`
/ `top_relevant_context_rows` onto `explanation.*`, and register it in place
of DummyScorer in app/dependencies.py. No caller outside this module needs
to change.
"""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from . import scoring_math

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
        anomaly_type = None
        if is_anomaly:
            if len(triggered) >= 3:
                anomaly_type = "MULTI_TYPOLOGY_CONVERGENCE"
            else:
                for sig in triggered:
                    if sig["id"] in _ANOMALY_TYPE_BY_TYPOLOGY:
                        anomaly_type = _ANOMALY_TYPE_BY_TYPOLOGY[sig["id"]]
                        break
                if anomaly_type is None:
                    anomaly_type = "STATISTICAL_OUTLIER"

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


class RPTScorer(BaseScorer):
    """Real SAP-RPT integration - NOT implemented yet.

    Wire this up following rpt_predict_demo.py:
      1. OAuth client-credentials token from AICORE_AUTH_URL (see get_token()).
      2. POST to f"{RPT_URL}/predict" with a payload shaped like
         CLASSIFICATION_PAYLOAD in rpt_predict_demo.py: `index_column`,
         `prediction_config.target_columns` (e.g. OVERALL_RISK_SCORE,
         RISK_TIER, IS_ANOMALY as regression/classification targets with the
         target value masked as "?" for the row being scored, plus labeled
         historical rows as in-context examples), `prediction_config.explanations`
         (top_column_scores / top_relevant_context_rows counts), and
         `data_schema` describing each column's dtype.
      3. Map the response's predicted target columns onto this method's
         `risk` dict, and `explanations.top_column_scores` /
         `explanations.top_relevant_context_rows` onto `explanation`.
      4. Swap DummyScorer -> RPTScorer in app/dependencies.py. No other file
         needs to change - every caller depends only on BaseScorer.score().
    """

    def score(self, ctx: ScoringContext) -> dict[str, Any]:
        raise NotImplementedError(
            "RPTScorer is not implemented - see rpt_predict_demo.py for the real "
            "SAP AI Core / RPT integration pattern this should follow. Use "
            "DummyScorer until a live model endpoint is wired in."
        )
