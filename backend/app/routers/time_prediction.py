"""Analyst open-case time-to-resolve prediction API.

Fully independent from the risk-assessment scoring API
(routers/risk_assessments.py, routers/ws.py) - separate router, separate
prefix, no shared schema/state. See app/time_prediction.py's module
docstring for the full design (HANA + SAP-RPT based, real historical
resolution-time data).
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from .. import time_prediction as tp

router = APIRouter(prefix="/time-prediction", tags=["time-prediction"])


@router.get("/analysts")
async def list_analysts() -> list[dict]:
    """Every ASSIGNED_TO with >=1 currently-open RISK_ALERTS case, ordered by
    open-case count descending - for populating an analyst picker.

    Response: [{"analyst": str, "open_case_count": int}, ...]
    """
    try:
        return await asyncio.to_thread(tp.list_analysts_with_open_cases)
    except tp.TimePredictionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/analysts/{analyst}/eta")
async def get_analyst_eta(analyst: str) -> dict:
    """Predicted total time-to-finish for every currently-open case assigned
    to `analyst`, one SAP-RPT regression call per case (run concurrently),
    summed into a total ETA.

    Response shape:
    {
      "analyst": "analyst_20",
      "open_case_count": 52,
      "total_predicted_hours": 812.34,
      "generated_at": "2026-07-31T12:00:00+00:00",
      "cases": [
        {
          "alert_id": 1234,
          "alert_type": "...",
          "alert_subtype": "...",
          "alert_priority": "...",
          "assigned_at": "2026-07-20T09:15:00+00:00",
          "predicted_hours": 41.2
        },
        ...
      ]
    }
    cases is sorted by predicted_hours descending (longest cases first).
    An analyst with zero currently-open cases (but who does exist in
    RISK_ALERTS) returns open_case_count=0/total_predicted_hours=0/cases=[]
    with a 200, not an error. An analyst name that matches no RISK_ALERTS row
    at all returns 404.
    """
    try:
        # Offload: this is a blocking HANA + (possibly 50+, concurrently
        # dispatched) SAP-RPT HTTP call chain - must not run inline on the
        # event loop, same fix pattern as risk_assessments.py's
        # update_review_status.
        return await asyncio.to_thread(tp.predict_analyst_eta, analyst)
    except tp.AnalystNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except tp.TimePredictionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
