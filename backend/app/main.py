"""SightLine backend - FastAPI application factory + lifespan wiring.

Run with:  uvicorn app.main:app --reload --port 8000   (from backend/)
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .data_store import DataStore
from .routers import risk_assessments, ws
from .scorer import BaseScorer, DummyScorer, RPTScorer
from .streaming import ConnectionManager, run_stream_simulator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sightline")


def _build_scorer() -> BaseScorer:
    """SCORER=rpt (default) -> real SAP-RPT calls with internal fallback to
    a DummyScorer on any failure (see RPTScorer.score in app/scorer.py).
    SCORER=dummy -> force the deterministic mock, e.g. for tests/offline dev."""
    if settings.SCORER == "dummy":
        logger.info("SCORER=dummy - using DummyScorer (mocked ML layer).")
        return DummyScorer()
    logger.info("SCORER=rpt - using RPTScorer (SAP-RPT %s).", settings.RPT_URL or "<no RPT URL configured>")
    return RPTScorer()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Loading TRANSACTION_FRAUD_FEATURES from %s ...", settings.TRANSACTION_FEATURES_PATH)
    app.state.data_store = DataStore(scorer=_build_scorer())
    app.state.ws_manager = ConnectionManager()
    logger.info("Loaded %d transactions.", len(app.state.data_store.df))

    app.state.stream_task = asyncio.create_task(
        run_stream_simulator(app.state.data_store, app.state.ws_manager)
    )
    try:
        yield
    finally:
        app.state.stream_task.cancel()
        try:
            await app.state.stream_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="SightLine Risk Assessment API",
    description=(
        "AML/fraud-triage backend for bank compliance analysts. Combines the "
        "deterministic T01-T13 typology/exposure layer (build_fraud_features.py) "
        "with a mocked ML/RPT scoring layer (see app/scorer.py). All responses "
        "conform to risk_assessment_schema.json / risk_assessment_stream_event_schema.json."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(risk_assessments.router)
app.include_router(ws.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}
