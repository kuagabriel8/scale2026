"""Unit tests for RPTScorer (app/scorer.py). Mocks the HTTP layer (httpx) -
never hits the live SAP-RPT endpoint - covering token caching, a successful
predict mapped onto the risk_assessment_schema.json shape, and the
fallback-to-DummyScorer path when the mocked call raises.
"""
from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

os.environ.setdefault("MAX_ROWS", "500")
os.environ.setdefault("SCORER", "dummy")  # app-level default; RPTScorer is exercised directly here

import httpx
import pytest

from app.scorer import DummyScorer, RPTScorer, ScoringContext


def _make_ctx(transaction_id: int = 12345, update_count: int = 0) -> ScoringContext:
    typology_signals = [
        {"id": "T01", "name": "Structuring", "category": "structure", "triggered": True, "points": 25},
        {"id": "T09", "name": "Sanctions Alias Match", "category": "sanctions", "triggered": False, "points": 0},
    ]
    return ScoringContext(
        transaction_id=transaction_id,
        total_typology_points=25,
        exposure_factor=5.0,
        typology_signals=typology_signals,
        update_count=update_count,
    )


@pytest.fixture()
def scorer():
    s = RPTScorer()
    # Avoid building the real ~200-row context pool from the parquet file in
    # every test - a couple of synthetic rows are enough to exercise mapping.
    s._get_context_pool = lambda: [
        {"transaction_id": 1, "T01_FLAG": 1, "OVERALL_RISK_SCORE": 40.0, "IS_ANOMALY": "0"},
        {"transaction_id": 2, "T01_FLAG": 0, "OVERALL_RISK_SCORE": 20.0, "IS_ANOMALY": "0"},
        {"transaction_id": 3, "T01_FLAG": 1, "OVERALL_RISK_SCORE": 80.0, "IS_ANOMALY": "1"},
    ]
    return s


REALISTIC_RESPONSE = {
    "explanations": {
        "top_column_scores": [
            {"EXPOSURE_FACTOR": 0.231, "T02_FLAG": 0.094, "T07_FLAG": 0.106, "TOTAL_TYPOLOGY_POINTS": 0.172}
        ],
        "top_relevant_context_rows": [[0, 2, 1]],
    },
    "id": "fake-id",
    "metadata": {"num_columns": 17, "num_predictions": 2, "num_query_rows": 1, "num_rows": 4},
    "predictions": [
        {
            "OVERALL_RISK_SCORE": [
                {"confidence": None, "confidence_interval": [25.2, 48.2], "prediction": 37.5}
            ],
            "IS_ANOMALY": [
                {"confidence": 0.82, "confidence_interval": None, "prediction": "1"},
                {"confidence": 0.18, "confidence_interval": None, "prediction": "0"},
            ],
            "transaction_id": 12345,
        }
    ],
    "status": {"code": 0, "message": "ok"},
}


def test_token_caching(scorer):
    """A second _get_token() call within the expiry window must not hit the
    network again."""
    fake_resp = MagicMock()
    fake_resp.raise_for_status.return_value = None
    fake_resp.json.return_value = {"access_token": "tok-123", "expires_in": 3600}

    with patch("app.scorer.httpx.post", return_value=fake_resp) as mock_post:
        token1 = scorer._get_token()
        token2 = scorer._get_token()

    assert token1 == token2 == "tok-123"
    mock_post.assert_called_once()


def test_token_refetches_after_expiry(scorer):
    fake_resp = MagicMock()
    fake_resp.raise_for_status.return_value = None
    fake_resp.json.return_value = {"access_token": "tok-1", "expires_in": 0}

    with patch("app.scorer.httpx.post", return_value=fake_resp):
        scorer._get_token()
    # expires_in=0 minus the refresh buffer is already in the past, so the
    # next call should re-fetch.
    fake_resp2 = MagicMock()
    fake_resp2.raise_for_status.return_value = None
    fake_resp2.json.return_value = {"access_token": "tok-2", "expires_in": 3600}
    with patch("app.scorer.httpx.post", return_value=fake_resp2) as mock_post2:
        token = scorer._get_token()
    assert token == "tok-2"
    mock_post2.assert_called_once()


def test_successful_predict_maps_to_schema_shape(scorer):
    scorer._get_token = lambda: "fake-token"
    fake_resp = MagicMock()
    fake_resp.raise_for_status.return_value = None
    fake_resp.json.return_value = REALISTIC_RESPONSE

    with patch.object(scorer._client, "post", return_value=fake_resp) as mock_post:
        result = scorer.score(_make_ctx())

    mock_post.assert_called_once()
    risk = result["risk"]
    explanation = result["explanation"]

    assert risk["overall_risk_score"] == 37.5
    assert risk["risk_tier"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert risk["is_anomaly"] is True  # top IS_ANOMALY candidate (confidence 0.82) predicts "1"
    assert risk["model_confidence"] == 0.82
    assert risk["anomaly_type"] is not None
    assert set(risk["component_scores"]).issuperset(
        {"amount_risk_score", "frequency_risk_score", "geography_risk_score"}
    )

    assert explanation["top_column_scores"][0]["column"] == "EXPOSURE_FACTOR"
    assert len(explanation["top_relevant_context_rows"]) == 3
    for row in explanation["top_relevant_context_rows"]:
        assert row["reference_id"].startswith("TXN-")
        assert row["outcome"] is None
        assert 0.0 <= row["similarity_score"] <= 1.0
    assert isinstance(explanation["narrative_text"], str) and explanation["narrative_text"]


def test_repeat_score_call_uses_result_cache(scorer):
    scorer._get_token = lambda: "fake-token"
    fake_resp = MagicMock()
    fake_resp.raise_for_status.return_value = None
    fake_resp.json.return_value = REALISTIC_RESPONSE
    ctx = _make_ctx()

    with patch.object(scorer._client, "post", return_value=fake_resp) as mock_post:
        result1 = scorer.score(ctx)
        result2 = scorer.score(ctx)

    assert result1 == result2
    mock_post.assert_called_once()  # second call served from the in-memory cache


def test_fallback_to_dummy_scorer_on_predict_failure(scorer):
    scorer._get_token = lambda: "fake-token"
    ctx = _make_ctx(transaction_id=999)

    with patch.object(scorer._client, "post", side_effect=httpx.ConnectError("boom")):
        result = scorer.score(ctx)

    expected = DummyScorer().score(ctx)
    assert result == expected


def test_fallback_on_malformed_response(scorer):
    scorer._get_token = lambda: "fake-token"
    ctx = _make_ctx(transaction_id=1000)
    fake_resp = MagicMock()
    fake_resp.raise_for_status.return_value = None
    fake_resp.json.return_value = {"predictions": []}  # missing expected keys -> IndexError

    with patch.object(scorer._client, "post", return_value=fake_resp):
        result = scorer.score(ctx)

    expected = DummyScorer().score(ctx)
    assert result == expected
