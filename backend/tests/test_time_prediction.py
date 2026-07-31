"""Unit + router tests for the analyst open-case time-prediction feature
(app/time_prediction.py, app/routers/time_prediction.py). Mocks HANA
(dbapi.connect) and the SAP-RPT HTTP calls - never requires live network/DB
access in the normal test run. A separate live smoke test (not part of this
file, see report) was run manually against the real HANA + RPT endpoints.
"""
from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

os.environ.setdefault("MAX_ROWS", "500")
os.environ.setdefault("SCORER", "dummy")

import pytest
from fastapi.testclient import TestClient

from app import time_prediction as tp
from app.main import app


# ---------------------------------------------------------------------------
# predict_analyst_eta orchestration (aggregation, zero-case, concurrency)
# ---------------------------------------------------------------------------

def _fake_case(alert_id: int) -> dict:
    return {
        "ALERT_ID": alert_id,
        "ALERT_TYPE": "STRUCTURING",
        "ALERT_SUBTYPE": "CASH",
        "ALERT_PRIORITY": "HIGH",
        "ALERT_SOURCE": "SCREENING_RULE",
        "RISK_DRIVERS": "high_amount, velocity",
        "ASSIGNED_AT": None,
    }


def test_total_hours_sums_mocked_per_case_predictions():
    cases = [_fake_case(1), _fake_case(2), _fake_case(3)]
    hours_by_id = {1: 10.0, 2: 5.5, 3: 20.25}

    with patch.object(tp, "analyst_exists", return_value=True), \
         patch.object(tp, "get_open_cases_for_analyst", return_value=cases), \
         patch.object(tp, "get_candidate_pool", return_value=[{"ALERT_ID": 100}] * 5), \
         patch.object(
             tp, "predict_case_duration",
             side_effect=lambda case, pool, context_n=None: hours_by_id[case["ALERT_ID"]],
         ) as mock_predict:
        result = tp.predict_analyst_eta("analyst_20")

    assert result["analyst"] == "analyst_20"
    assert result["open_case_count"] == 3
    assert result["total_predicted_hours"] == sum(hours_by_id.values())
    assert mock_predict.call_count == 3  # once per open case
    # Sorted by predicted_hours descending.
    assert [c["predicted_hours"] for c in result["cases"]] == sorted(hours_by_id.values(), reverse=True)


def test_zero_open_cases_returns_clean_empty_result_not_error():
    with patch.object(tp, "analyst_exists", return_value=True), \
         patch.object(tp, "get_open_cases_for_analyst", return_value=[]):
        result = tp.predict_analyst_eta("analyst_with_none_open")

    assert result == {
        "analyst": "analyst_with_none_open",
        "open_case_count": 0,
        "total_predicted_hours": 0.0,
        "generated_at": result["generated_at"],
        "cases": [],
    }


def test_unknown_analyst_raises_not_found_error():
    with patch.object(tp, "analyst_exists", return_value=False):
        with pytest.raises(tp.AnalystNotFoundError):
            tp.predict_analyst_eta("nobody_such_analyst")


def test_insufficient_candidate_pool_raises_time_prediction_error():
    with patch.object(tp, "analyst_exists", return_value=True), \
         patch.object(tp, "get_open_cases_for_analyst", return_value=[_fake_case(1)]), \
         patch.object(tp, "get_candidate_pool", return_value=[{"ALERT_ID": 1}]):  # only 1 row
        with pytest.raises(tp.TimePredictionError):
            tp.predict_analyst_eta("analyst_20")


def test_per_case_predictions_run_concurrently():
    """A slow (0.2s) mocked predictor over 6 cases must complete in well
    under 6 * 0.2s serial time if truly run concurrently."""
    cases = [_fake_case(i) for i in range(6)]

    def _slow_predict(case, pool, context_n=None):
        time.sleep(0.2)
        return 1.0

    with patch.object(tp, "analyst_exists", return_value=True), \
         patch.object(tp, "get_open_cases_for_analyst", return_value=cases), \
         patch.object(tp, "get_candidate_pool", return_value=[{"ALERT_ID": 100}] * 5), \
         patch.object(tp, "predict_case_duration", side_effect=_slow_predict):
        start = time.monotonic()
        result = tp.predict_analyst_eta("analyst_20")
        elapsed = time.monotonic() - start

    assert result["open_case_count"] == 6
    assert elapsed < 0.2 * 6  # would be ~1.2s serial; concurrent should be ~0.2-0.4s


def test_one_failed_case_does_not_fail_whole_batch():
    cases = [_fake_case(1), _fake_case(2)]

    def _maybe_fail(case, pool, context_n=None):
        if case["ALERT_ID"] == 1:
            raise RuntimeError("boom")
        return 3.0

    with patch.object(tp, "analyst_exists", return_value=True), \
         patch.object(tp, "get_open_cases_for_analyst", return_value=cases), \
         patch.object(tp, "get_candidate_pool", return_value=[{"ALERT_ID": 100}] * 5), \
         patch.object(tp, "predict_case_duration", side_effect=_maybe_fail):
        result = tp.predict_analyst_eta("analyst_20")

    assert len(result["cases"]) == 1
    assert result["cases"][0]["alert_id"] == 2


def test_all_cases_failing_raises_time_prediction_error():
    cases = [_fake_case(1), _fake_case(2)]

    with patch.object(tp, "analyst_exists", return_value=True), \
         patch.object(tp, "get_open_cases_for_analyst", return_value=cases), \
         patch.object(tp, "get_candidate_pool", return_value=[{"ALERT_ID": 100}] * 5), \
         patch.object(tp, "predict_case_duration", side_effect=RuntimeError("boom")):
        with pytest.raises(tp.TimePredictionError):
            tp.predict_analyst_eta("analyst_20")


# ---------------------------------------------------------------------------
# predict_case_duration (RPT payload / response mapping)
# ---------------------------------------------------------------------------

def test_predict_case_duration_maps_rpt_response():
    target = _fake_case(42)
    pool = [
        {**_fake_case(1), "ASSIGNED_AT": None, "ASSESSMENT_DURATION_HOURS": 12.0},
        {**_fake_case(2), "ASSIGNED_AT": None, "ASSESSMENT_DURATION_HOURS": 8.0},
        {**_fake_case(3), "ASSIGNED_AT": None, "ASSESSMENT_DURATION_HOURS": 20.0},
    ]
    fake_response = {
        "predictions": [
            {"ASSESSMENT_DURATION_HOURS": [{"prediction": 17.25, "confidence": None}]}
        ]
    }

    with patch.object(tp, "_get_token", return_value="fake-token"), \
         patch.object(tp.httpx, "post") as mock_post:
        mock_post.return_value = MagicMock(json=lambda: fake_response, raise_for_status=lambda: None)
        hours = tp.predict_case_duration(target, pool)

    assert hours == 17.25


def test_predict_case_duration_requires_at_least_3_candidates():
    with pytest.raises(tp.TimePredictionError):
        tp.predict_case_duration(_fake_case(1), [{"ALERT_ID": 1}])


# ---------------------------------------------------------------------------
# Router tests (unknown analyst -> 404, zero-case -> 200 empty)
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_router_unknown_analyst_returns_404(client):
    with patch.object(tp, "analyst_exists", return_value=False):
        resp = client.get("/time-prediction/analysts/nobody_such_analyst/eta")
    assert resp.status_code == 404


def test_router_zero_open_cases_returns_200_empty(client):
    with patch.object(tp, "analyst_exists", return_value=True), \
         patch.object(tp, "get_open_cases_for_analyst", return_value=[]):
        resp = client.get("/time-prediction/analysts/analyst_with_none_open/eta")
    assert resp.status_code == 200
    body = resp.json()
    assert body["open_case_count"] == 0
    assert body["total_predicted_hours"] == 0
    assert body["cases"] == []


def test_router_list_analysts(client):
    fake = [{"analyst": "analyst_20", "open_case_count": 52}]
    with patch.object(tp, "list_analysts_with_open_cases", return_value=fake):
        resp = client.get("/time-prediction/analysts")
    assert resp.status_code == 200
    assert resp.json() == fake
