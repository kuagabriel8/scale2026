"""Contract + behavior tests for the SightLine backend.

Uses a small MAX_ROWS cap (set below, before importing the app) so the test
suite loads quickly instead of the full 150k-row parquet file.
"""
from __future__ import annotations

import os

os.environ.setdefault("MAX_ROWS", "500")

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schema_validation import validate_assessment, validate_stream_event


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200


def test_list_risk_assessments_validates_against_schema(client):
    resp = client.get("/risk-assessments?page=1&page_size=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["page"] == 1
    assert body["page_size"] == 10
    assert len(body["items"]) == 10
    for item in body["items"]:
        validate_assessment(item)  # raises on contract violation


def test_list_is_sorted_by_severity_desc_by_default(client):
    resp = client.get("/risk-assessments?page=1&page_size=50")
    items = resp.json()["items"]
    severities = [
        max(i["risk"]["overall_risk_score"], i["exposure"]["typology_strength_points"])
        for i in items
    ]
    assert severities == sorted(severities, reverse=True)


def test_filter_by_risk_tier(client):
    resp = client.get("/risk-assessments?risk_tier=CRITICAL&page_size=50")
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert item["risk"]["risk_tier"] == "CRITICAL"


def test_filter_by_category(client):
    resp = client.get("/risk-assessments?category=sanctions&page_size=50")
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        cats_triggered = {s["category"] for s in item["typology_signals"] if s["triggered"]}
        assert "sanctions" in cats_triggered


def test_get_single_assessment(client):
    listed = client.get("/risk-assessments?page_size=1").json()["items"][0]
    tid = listed["transaction_id"]
    resp = client.get(f"/risk-assessments/{tid}")
    assert resp.status_code == 200
    body = resp.json()
    validate_assessment(body)
    assert body["transaction_id"] == tid


def test_get_missing_assessment_404(client):
    resp = client.get("/risk-assessments/999999999")
    assert resp.status_code == 404


def test_patch_review_status_updates_and_validates(client):
    listed = client.get("/risk-assessments?page_size=1").json()["items"][0]
    tid = listed["transaction_id"]

    resp = client.patch(
        f"/risk-assessments/{tid}/review",
        json={"review_status": "IN_REVIEW", "reviewed_by": "analyst.jones"},
    )
    assert resp.status_code == 200
    body = resp.json()
    validate_assessment(body)
    assert body["governance"]["review_status"] == "IN_REVIEW"
    assert body["governance"]["reviewed_by"] == "analyst.jones"
    assert body["governance"]["requires_human_review"] is True


def test_patch_review_status_missing_404(client):
    resp = client.patch(
        "/risk-assessments/999999999/review",
        json={"review_status": "CLEARED"},
    )
    assert resp.status_code == 404


def test_websocket_receives_assessment_created(client):
    with client.websocket_connect("/ws/risk-stream") as ws:
        data = ws.receive_json()
        validate_stream_event(data)
        assert data["event_type"] in {"ASSESSMENT_CREATED", "ASSESSMENT_UPDATED"}
        assert data["sequence"] >= 0
        validate_assessment(data["assessment"])


def test_websocket_review_status_changed_broadcast(client):
    listed = client.get("/risk-assessments?page_size=1").json()["items"][0]
    tid = listed["transaction_id"]

    with client.websocket_connect("/ws/risk-stream") as ws:
        # Drain a couple of simulator events first, then trigger a review
        # change and confirm it's broadcast as REVIEW_STATUS_CHANGED.
        client.patch(
            f"/risk-assessments/{tid}/review",
            json={"review_status": "ESCALATED", "reviewed_by": "analyst.lee"},
        )
        found = False
        for _ in range(20):
            data = ws.receive_json()
            if data["event_type"] == "REVIEW_STATUS_CHANGED" and data["assessment"]["transaction_id"] == tid:
                found = True
                assert data["assessment"]["governance"]["review_status"] == "ESCALATED"
                break
        assert found, "did not observe REVIEW_STATUS_CHANGED broadcast"
