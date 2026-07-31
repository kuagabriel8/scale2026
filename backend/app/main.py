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
from .scorer import BaseScorer, DummyScorer, RPTScorer, SklearnScorer
from .streaming import ConnectionManager, run_stream_simulator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sightline")

# Available scoring models a caller can pick per-request via `?model=`/WS
# `{"model": ...}` - see app/routers/risk_assessments.py's GET /models.
AVAILABLE_MODELS = [
    {
        "id": "rpt",
        "label": "SAP RPT 1.5",
        "description": (
            "SAP-RPT (sap-rpt-1.5-large) tabular foundation model, in-context "
            "regression/classification over a few-shot proxy-labeled pool "
            "(no gradient training). Falls back to the deterministic mock "
            "on any live-call failure."
        ),
    },
    {
        "id": "sklearn",
        "label": "scikit-learn (GBR + RF)",
        "description": (
            "Offline-trained GradientBoostingRegressor (risk score) + "
            "RandomForestClassifier (anomaly probability) over 41 engineered "
            "features. Local/offline, no network calls - see "
            "train_sklearn_scorer.py / sklearn_model_report.txt."
        ),
    },
    {
        "id": "dummy",
        "label": "Deterministic mock",
        "description": (
            "Fabricated-but-plausible scorer correlated with the rule-layer "
            "typology points - used for tests/offline dev, no real model."
        ),
    },
]


def _build_scorers() -> dict[str, BaseScorer]:
    """Constructs all three scorers unconditionally so a caller can pick any
    of them per-request (see DataStore.list_assessments/get_assessment's
    `model` param) - settings.SCORER only selects which one is the
    *default* (used by the shared background stream simulator and any
    request without an explicit `?model=` choice)."""
    logger.info("Building scorers: rpt, sklearn, dummy (default=%s).", settings.SCORER)
    return {
        "rpt": RPTScorer(),
        "sklearn": SklearnScorer(),
        "dummy": DummyScorer(),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Loading TRANSACTION_FRAUD_FEATURES from %s ...", settings.TRANSACTION_FEATURES_PATH)
    app.state.data_store = DataStore(scorers=_build_scorers(), default_scorer=settings.SCORER)
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


@app.get("/models", tags=["meta"])
def list_models() -> list[dict]:
    """Available scoring models a caller can pass as `?model=` on
    GET /risk-assessments[/{transaction_id}] or as `{"model": ...}` in a WS
    `{"type": "get", ...}` message. `id` is the exact string to pass."""
    return AVAILABLE_MODELS
