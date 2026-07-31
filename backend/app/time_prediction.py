"""Analyst open-case time-to-resolve prediction (SAP-RPT regression).

Independent feature, separate from the risk-assessment scoring API
(app/scorer.py, app/data_store.py, risk_assessment_schema.json) - this module
never touches that schema, its parquet data, or its routers. Given an analyst
(a RISK_ALERTS.ASSIGNED_TO value, e.g. "analyst_20"), it predicts how long it
will take that analyst to finish ALL of their currently-open cases (alerts
assigned to them but not yet resolved), one SAP-RPT regression call per open
case, summed into a total ETA.

This ports/reuses the core prediction logic from ../time_prediction.py (a
working single-case CLI script written against real HANA TEAM_03.RISK_ALERTS
data). Two things in that script were broken/inconsistent with the rest of
this codebase and are fixed here rather than ported as-is:

  1. Credentials: ../time_prediction.py's load_credentials()/
     get_hana_connection() expect ../hanadb/team_03_credentials.json, which
     does not exist anywhere in this repo. This module instead connects the
     same way ../fraud_data_cleaning.py's connect() does - plain HANA_HOST/
     HANA_PORT/HANA_USER/HANA_PASSWORD/HANA_SCHEMA env vars, read via
     app/config.py's Settings (which already dual-loads the repo-root and
     backend .env files).
  2. Target-case selection: the CLI script's main() loads one hardcoded
     target case from ../data/time_prediction_sample.json (also missing from
     the repo). This module's target cases are the analyst's REAL open
     alerts (ASSIGNED_AT IS NOT NULL AND RESOLVED_AT IS NULL AND
     ASSIGNED_TO = ?), queried live from RISK_ALERTS - see
     get_open_cases_for_analyst below.

The pure similarity-ranking / RPT-payload-building logic is NOT reimplemented
here - `select_similar()` and `build_payload()` (which itself uses
`historical_to_rpt()`/`target_to_rpt()`) are imported unmodified from
../time_prediction.py, added to sys.path via REPO_ROOT (the same repo-root
resolution app/config.py already uses for topfraudandtables.json etc). Only
`duration_hours()` is additionally reused; everything else in this module
(HANA queries, OAuth token caching, the /predict HTTP call, and the
concurrent per-analyst orchestration) is new, written to fit this backend's
conventions (httpx instead of requests, app/config.py's Settings instead of
os.getenv/require_env, a small independent token-cache mirroring
app/scorer.py's RPTScorer._get_token rather than sharing its instance state
directly - see docstring note below on why a full share wasn't clean).
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import httpx
from hdbcli import dbapi

from .config import REPO_ROOT, settings

logger = logging.getLogger("sightline.time_prediction")

# Make the repo-root ../time_prediction.py importable as a plain top-level
# module (it has no package __init__.py and isn't part of the backend/
# package tree). Only pure helper functions are pulled from it - see module
# docstring above for exactly which ones and why.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from time_prediction import (  # noqa: E402 - import after sys.path fixup, deliberate
    build_payload,
    duration_hours,
    select_similar,
)


class TimePredictionError(Exception):
    """Clean, expected failure mode (insufficient historical data, missing
    HANA config, every per-case RPT call failing, etc.) - routers should
    catch this and map it to a 4xx/5xx response, never let it surface as an
    unhandled 500 traceback."""


class AnalystNotFoundError(TimePredictionError):
    """Raised when `analyst` matches zero RISK_ALERTS rows at all (as opposed
    to a real analyst who simply has zero currently-open cases right now -
    see predict_analyst_eta)."""


_OPEN_CASE_WHERE = "ASSIGNED_AT IS NOT NULL AND RESOLVED_AT IS NULL AND ASSIGNED_TO IS NOT NULL"

_TARGET_COLUMNS = (
    "ALERT_ID, ALERT_TYPE, ALERT_SUBTYPE, ALERT_PRIORITY, ALERT_SOURCE, "
    "RISK_DRIVERS, ASSIGNED_AT"
)


# ============================================================
# HANA connection (.env-based - see module docstring point 1)
# ============================================================

def get_hana_connection():
    if not settings.HANA_HOST or not settings.HANA_USER or not settings.HANA_PASSWORD:
        raise TimePredictionError(
            "HANA connection is not configured: set HANA_HOST/HANA_PORT/HANA_USER/"
            "HANA_PASSWORD (and optionally HANA_SCHEMA, default TEAM_03) in the "
            "repo-root or backend/.env - see fraud_data_cleaning.py's connect() for "
            "the same pattern."
        )
    try:
        return dbapi.connect(
            address=settings.HANA_HOST,
            port=int(settings.HANA_PORT),
            user=settings.HANA_USER,
            password=settings.HANA_PASSWORD,
            encrypt=True,
            sslValidateCertificate=False,
        )
    except Exception as exc:  # noqa: BLE001 - re-raise as a clear, catchable error
        raise TimePredictionError(f"Could not connect to HANA: {exc}") from exc


# ============================================================
# Analyst / open-case queries
# ============================================================

def list_analysts_with_open_cases() -> list[dict[str, Any]]:
    """One HANA query: every distinct ASSIGNED_TO with >=1 currently-open
    case (assigned, unresolved), ordered by open-case count descending - for
    populating a picker."""
    conn = get_hana_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT ASSIGNED_TO, COUNT(*) AS OPEN_CASE_COUNT
            FROM {settings.HANA_SCHEMA}.RISK_ALERTS
            WHERE {_OPEN_CASE_WHERE}
            GROUP BY ASSIGNED_TO
            ORDER BY OPEN_CASE_COUNT DESC
            """
        )
        rows = cur.fetchall()
        return [{"analyst": str(r[0]), "open_case_count": int(r[1])} for r in rows]
    finally:
        conn.close()


def analyst_exists(analyst: str) -> bool:
    """True iff ANY RISK_ALERTS row (open or resolved) has this ASSIGNED_TO -
    lets predict_analyst_eta/the router distinguish "unknown analyst" (404)
    from "known analyst, zero open cases right now" (200, empty result)."""
    conn = get_hana_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT COUNT(*) FROM {settings.HANA_SCHEMA}.RISK_ALERTS WHERE ASSIGNED_TO = ?",
            (analyst,),
        )
        count = cur.fetchone()[0]
        return bool(count and count > 0)
    finally:
        conn.close()


def get_open_cases_for_analyst(analyst: str) -> list[dict[str, Any]]:
    """The analyst's real open alerts (see module docstring point 2) - same
    columns ../time_prediction.py's get_target_alert/get_candidates already
    select (minus RESOLVED_AT, which is NULL for all of these by definition)."""
    conn = get_hana_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT {_TARGET_COLUMNS}
            FROM {settings.HANA_SCHEMA}.RISK_ALERTS
            WHERE {_OPEN_CASE_WHERE} AND ASSIGNED_TO = ?
            ORDER BY ASSIGNED_AT ASC
            """,
            (analyst,),
        )
        names = [c[0] for c in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def get_candidate_pool(limit: int | None = None) -> list[dict[str, Any]]:
    """The completed-historical-alert pool (ASSIGNED_AT/RESOLVED_AT both set,
    valid duration) - ../time_prediction.py's get_candidates() query, minus
    the single-target-id exclusion (not meaningful here: this pool is fetched
    ONCE per request and shared across every one of the analyst's open cases,
    not once per case - an analyst can have 50+ open cases, so refetching a
    ~500-row pool per case would be wasteful). Completed rows are excluded
    from the open-case set automatically since RESOLVED_AT IS NOT NULL there
    and IS NULL for open cases, so no explicit exclusion is needed anyway."""
    limit = limit or settings.TIME_PREDICTION_CANDIDATE_ROWS
    conn = get_hana_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT ALERT_ID, ALERT_TYPE, ALERT_SUBTYPE, ALERT_PRIORITY, ALERT_SOURCE,
                   RISK_DRIVERS, ASSIGNED_AT, RESOLVED_AT
            FROM {settings.HANA_SCHEMA}.RISK_ALERTS
            WHERE ASSIGNED_AT IS NOT NULL
              AND RESOLVED_AT IS NOT NULL
              AND RESOLVED_AT >= ASSIGNED_AT
            ORDER BY RESOLVED_AT DESC
            LIMIT {int(limit)}
            """
        )
        names = [c[0] for c in cur.description]
        result: list[dict[str, Any]] = []
        for raw in cur.fetchall():
            row = dict(zip(names, raw))
            duration = duration_hours(row["ASSIGNED_AT"], row["RESOLVED_AT"])
            if duration is None:
                continue
            row["ASSESSMENT_DURATION_HOURS"] = round(duration, 4)
            result.append(row)
        return result
    finally:
        conn.close()


# ============================================================
# OAuth token (small independent cache) + RPT /predict call
# ============================================================
# app/scorer.py's RPTScorer._get_token already implements this exact caching
# pattern (same AICORE credentials, same auth endpoint) but it is bound to a
# RPTScorer *instance* (self._token/_token_lock) that's constructed once in
# main.py's lifespan for the risk-assessment feature - sharing it directly
# would mean importing/instantiating scorer.RPTScorer here (or reaching into
# app.state from a plain module function), which crosses this feature's
# "fully separate from risk-assessment" boundary for no real benefit, since
# the two features don't actually share a token (different process-level
# concern, and AI Core issues short-lived tokens cheaply). So: a small
# independent module-level cache, same logic, same safety-buffer setting.

_token_lock = threading.Lock()
_token: str | None = None
_token_expiry_monotonic: float = 0.0


def _get_token() -> str:
    global _token, _token_expiry_monotonic
    with _token_lock:
        if _token is not None and time.monotonic() < _token_expiry_monotonic:
            return _token
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
        _token = token
        _token_expiry_monotonic = time.monotonic() + max(
            0.0, expires_in - settings.RPT_TOKEN_REFRESH_BUFFER_SECONDS
        )
        return token


def _call_rpt_predict(payload: dict[str, Any]) -> dict[str, Any]:
    resp = httpx.post(
        f"{settings.RPT_URL}/predict",
        headers={
            "Authorization": f"Bearer {_get_token()}",
            "AI-Resource-Group": settings.AICORE_RESOURCE_GROUP,
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=settings.RPT_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    return resp.json()


# ============================================================
# Per-case prediction
# ============================================================

def predict_case_duration(
    target: dict[str, Any],
    candidate_pool: list[dict[str, Any]],
    context_n: int | None = None,
) -> float:
    """Predicts ASSESSMENT_DURATION_HOURS for ONE open case, reusing
    select_similar() + build_payload() from ../time_prediction.py unchanged.
    Raises TimePredictionError if candidate_pool has fewer than 3 usable
    historical rows (mirrors ../time_prediction.py's own `--context`/
    candidate-count validation in main())."""
    if len(candidate_pool) < 3:
        raise TimePredictionError(
            f"Only {len(candidate_pool)} completed historical RISK_ALERTS cases "
            "available; need at least 3 to run a similarity-based RPT regression."
        )

    context_n = context_n or settings.TIME_PREDICTION_CONTEXT_ROWS
    historical = select_similar(target, candidate_pool, context_n)
    payload = build_payload(historical, target)
    response = _call_rpt_predict(payload)

    predictions = response["predictions"][0]
    duration_pred = predictions["ASSESSMENT_DURATION_HOURS"][0]
    hours = float(duration_pred["prediction"])
    return max(0.0, hours)


# ============================================================
# Per-analyst orchestration (concurrent per-case RPT calls)
# ============================================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _case_summary(case: dict[str, Any], predicted_hours: float) -> dict[str, Any]:
    assigned_at = case.get("ASSIGNED_AT")
    return {
        "alert_id": int(case["ALERT_ID"]),
        "alert_type": case.get("ALERT_TYPE"),
        "alert_subtype": case.get("ALERT_SUBTYPE"),
        "alert_priority": case.get("ALERT_PRIORITY"),
        "assigned_at": assigned_at.isoformat() if hasattr(assigned_at, "isoformat") else assigned_at,
        "predicted_hours": round(predicted_hours, 2),
    }


def predict_analyst_eta(
    analyst: str,
    context_n: int | None = None,
    candidate_rows: int | None = None,
) -> dict[str, Any]:
    """Orchestrates the full per-analyst ETA prediction:

      1. Confirms the analyst is a real ASSIGNED_TO value at all (raises
         AnalystNotFoundError otherwise - the router turns this into a 404).
      2. Fetches the analyst's real open cases. Zero open cases is a normal,
         successful result (open_case_count=0, total_predicted_hours=0,
         cases=[]) - not an error.
      3. Fetches the shared historical candidate pool ONCE (not per case).
      4. Runs predict_case_duration() for every open case CONCURRENTLY via a
         thread pool (bounded by settings.TIME_PREDICTION_MAX_CONCURRENCY) -
         each call is an independent blocking HTTP request, and an analyst
         can have 50+ open cases, so serial calls at ~1-2s each would take
         over a minute.
      5. Returns cases sorted by predicted_hours descending (longest first).

    This function itself is synchronous/blocking (HANA + HTTP calls) - the
    router offloads it via asyncio.to_thread so it never blocks the FastAPI
    event loop, same fix pattern already used for
    routers/risk_assessments.py's update_review_status.
    """
    if not analyst_exists(analyst):
        raise AnalystNotFoundError(
            f"Unknown analyst {analyst!r}: no RISK_ALERTS row has this ASSIGNED_TO value."
        )

    open_cases = get_open_cases_for_analyst(analyst)
    if not open_cases:
        return {
            "analyst": analyst,
            "open_case_count": 0,
            "total_predicted_hours": 0.0,
            "generated_at": _now_iso(),
            "cases": [],
        }

    candidate_pool = get_candidate_pool(candidate_rows)
    if len(candidate_pool) < 3:
        raise TimePredictionError(
            f"Only {len(candidate_pool)} completed historical RISK_ALERTS cases "
            "available; need at least 3 to run a similarity-based RPT regression."
        )

    n = len(open_cases)
    max_workers = max(1, min(settings.TIME_PREDICTION_MAX_CONCURRENCY, n))
    results: list[dict[str, Any] | None] = [None] * n
    errors: list[Exception] = []

    def _run_one(i: int, case: dict[str, Any]) -> None:
        try:
            hours = predict_case_duration(case, candidate_pool, context_n)
            results[i] = _case_summary(case, hours)
        except Exception as exc:  # noqa: BLE001 - collect, don't let one bad case kill the batch
            logger.warning(
                "predict_analyst_eta: prediction failed for analyst=%s alert_id=%s: %s",
                analyst, case.get("ALERT_ID"), exc,
            )
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_run_one, i, case) for i, case in enumerate(open_cases)]
        for f in futures:
            f.result()

    cases = [r for r in results if r is not None]

    if not cases and errors:
        # Every single per-case prediction failed - a real error, not a
        # quiet empty-result success.
        raise TimePredictionError(
            f"All {n} per-case RPT predictions failed for analyst {analyst!r}; "
            f"first error: {errors[0]}"
        )

    cases.sort(key=lambda c: c["predicted_hours"], reverse=True)
    total_hours = round(sum(c["predicted_hours"] for c in cases), 2)

    return {
        "analyst": analyst,
        "open_case_count": n,
        "total_predicted_hours": total_hours,
        "generated_at": _now_iso(),
        "cases": cases,
    }
