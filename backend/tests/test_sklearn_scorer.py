"""Unit tests for SklearnScorer (app/scorer.py). Fully local/offline - no
network mocking needed, unlike test_rpt_scorer.py. Verifies a schema-valid
{risk, explanation} for real transaction_ids, that risk_tier bucketing
matches scoring_math.risk_tier(), and that an unknown transaction_id is
handled with a clear error rather than silently producing wrong data.
"""
from __future__ import annotations

import os

os.environ.setdefault("MAX_ROWS", "500")
os.environ.setdefault("SCORER", "dummy")  # app-level default; SklearnScorer is exercised directly here

import pytest

from app import scoring_math
from app.scorer import ScoringContext, SklearnScorer


@pytest.fixture(scope="module")
def scorer() -> SklearnScorer:
    return SklearnScorer()


def _ctx_for(scorer: SklearnScorer, transaction_id: int) -> ScoringContext:
    row = scorer._rows_by_txn[transaction_id]
    total_points = int(row["TOTAL_TYPOLOGY_POINTS"])
    typology_signals = [
        {
            "id": "T01",
            "name": "Structuring",
            "category": "structure",
            "triggered": bool(row.get("T01_STRUCTURING_FLAG")),
            "points": 25 if row.get("T01_STRUCTURING_FLAG") else 0,
        },
        {
            "id": "T09",
            "name": "Sanctions Alias Match",
            "category": "sanctions",
            "triggered": bool(row.get("T09_SANCTIONS_FLAG")),
            "points": 40 if row.get("T09_SANCTIONS_FLAG") else 0,
        },
    ]
    return ScoringContext(
        transaction_id=transaction_id,
        total_typology_points=total_points,
        exposure_factor=float(row["EXPOSURE_FACTOR"]),
        typology_signals=typology_signals,
        update_count=0,
    )


def test_loads_artifacts_and_joined_data(scorer):
    assert scorer.model_version == "sklearn-gbr-rfc-v1"
    assert len(scorer._rows_by_txn) > 0


def test_score_real_transaction_ids_produce_valid_shape(scorer):
    sample_ids = list(scorer._rows_by_txn.keys())[:10]
    for tid in sample_ids:
        ctx = _ctx_for(scorer, tid)
        result = scorer.score(ctx)
        risk, explanation = result["risk"], result["explanation"]

        assert 0.0 <= risk["overall_risk_score"] <= 100.0
        assert risk["risk_tier"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
        assert risk["risk_tier"] == scoring_math.risk_tier(risk["overall_risk_score"])
        assert 0.0 <= risk["model_confidence"] <= 1.0
        assert isinstance(risk["is_anomaly"], bool)
        assert set(risk["component_scores"]) == {
            "amount_risk_score", "frequency_risk_score", "geography_risk_score",
            "counterparty_risk_score", "pattern_risk_score", "velocity_risk_score",
        }
        for v in risk["component_scores"].values():
            assert 0.0 <= v <= 100.0

        assert len(explanation["top_column_scores"]) <= 4
        for c in explanation["top_column_scores"]:
            assert "column" in c and "contribution_score" in c
        for row in explanation["top_relevant_context_rows"]:
            assert row["reference_id"].startswith("TXN-")
            assert 0.0 <= row["similarity_score"] <= 1.0
            assert row["outcome"] is None
        assert isinstance(explanation["narrative_text"], str) and explanation["narrative_text"]
        assert "scikit-learn" in explanation["narrative_text"]


def test_is_anomaly_implies_anomaly_type(scorer):
    sample_ids = list(scorer._rows_by_txn.keys())[:50]
    for tid in sample_ids:
        ctx = _ctx_for(scorer, tid)
        result = scorer.score(ctx)
        risk = result["risk"]
        if risk["is_anomaly"]:
            assert risk["anomaly_type"] is not None
        else:
            assert risk["anomaly_type"] is None


def test_unknown_transaction_id_raises_clearly(scorer):
    ctx = ScoringContext(
        transaction_id=-999999999,
        total_typology_points=10,
        exposure_factor=1.0,
        typology_signals=[],
        update_count=0,
    )
    with pytest.raises(ValueError, match="not found"):
        scorer.score(ctx)
