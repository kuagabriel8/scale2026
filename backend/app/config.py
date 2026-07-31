"""Runtime configuration for the SightLine backend.

All paths resolve relative to the repo root (the parent of this `backend/`
folder), since the data files (`cleaned_data/`, `topfraudandtables.json`,
`risk_assessment_schema.json`) live at the repo root, not inside `backend/`.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent

# Load the repo-root .env first (this is where AICORE_CLIENT_ID/SECRET,
# AICORE_AUTH_URL, AICORE_RESOURCE_GROUP, and the RPT deployment URL live -
# shared with the HANA/AI Core scripts at the repo root), then optionally
# overlay backend/.env if present so backend-specific overrides (e.g. a
# different SCORER or MODEL_VERSION for local dev) win without duplicating
# secrets into a second file.
load_dotenv(REPO_ROOT / ".env")
load_dotenv(BACKEND_DIR / ".env", override=True)


def _bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    # --- Data sources ---
    CLEANED_DATA_DIR: Path = Path(os.getenv("CLEANED_DATA_DIR", REPO_ROOT / "cleaned_data"))
    TRANSACTION_FEATURES_PATH: Path = CLEANED_DATA_DIR / "TRANSACTION_FRAUD_FEATURES.parquet"
    COMPANY_FEATURES_PATH: Path = CLEANED_DATA_DIR / "COMPANY_FRAUD_FEATURES.parquet"
    TRANSACTIONS_PATH: Path = CLEANED_DATA_DIR / "TRANSACTIONS.parquet"
    COUNTRIES_PATH: Path = CLEANED_DATA_DIR / "COUNTRIES.parquet"
    BASELINES_PATH: Path = CLEANED_DATA_DIR / "TRANSACTION_BASELINES.parquet"

    # --- Offline-trained scikit-learn scorer artifacts (see
    # ../train_sklearn_scorer.py / ../sklearn_model_report.txt). Not
    # retrained here - app/scorer.py's SklearnScorer just loads these. ---
    ML_MODELS_DIR: Path = Path(os.getenv("ML_MODELS_DIR", REPO_ROOT / "ml_models"))
    SKLEARN_REGRESSOR_PATH: Path = ML_MODELS_DIR / "sklearn_risk_regressor.joblib"
    SKLEARN_CLASSIFIER_PATH: Path = ML_MODELS_DIR / "sklearn_anomaly_classifier.joblib"
    SKLEARN_FEATURE_SCHEMA_PATH: Path = ML_MODELS_DIR / "feature_schema.json"

    TYPOLOGY_DEFS_PATH: Path = Path(os.getenv("TYPOLOGY_DEFS_PATH", REPO_ROOT / "topfraudandtables.json"))
    RISK_ASSESSMENT_SCHEMA_PATH: Path = Path(
        os.getenv("RISK_ASSESSMENT_SCHEMA_PATH", REPO_ROOT / "risk_assessment_schema.json")
    )
    STREAM_EVENT_SCHEMA_PATH: Path = Path(
        os.getenv("STREAM_EVENT_SCHEMA_PATH", REPO_ROOT / "risk_assessment_stream_event_schema.json")
    )

    # --- Row cap for dev convenience (0/None = load all 150k rows) ---
    MAX_ROWS: int = int(os.getenv("MAX_ROWS", "0")) or None

    # --- CORS ---
    CORS_ORIGINS: list[str] = [
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
        if origin.strip()
    ]

    # --- Simulated live-ingestion stream ---
    # Number of ASSESSMENT_CREATED events emitted per second by the
    # background simulator. 5/sec is a reasonable "a few per second" demo
    # pace per the task brief.
    STREAM_EVENTS_PER_SECOND: float = float(os.getenv("STREAM_EVENTS_PER_SECOND", "5"))
    # Roughly 1 in N emitted events is instead a re-emit (ASSESSMENT_UPDATED)
    # of an already-seen transaction, simulating a re-score.
    STREAM_RESCORE_EVERY_N: int = int(os.getenv("STREAM_RESCORE_EVERY_N", "7"))
    # Whether the simulator loops back to the start after exhausting all rows
    # (keeps a demo running indefinitely) or stops.
    STREAM_LOOP: bool = _bool("STREAM_LOOP", True)

    # --- Dev-mode runtime schema assertions ---
    # When true, every assessment dict built by the data layer is validated
    # against risk_assessment_schema.json before being returned/emitted.
    # Adds overhead - keep on in dev, can disable in a hypothetical
    # production deployment via env var.
    VALIDATE_RESPONSES: bool = _bool("VALIDATE_RESPONSES", True)

    # --- ML/RPT scoring layer selection ---
    # This picks the *default* scorer only - the one used for the shared
    # background stream simulator (no per-viewer concept there) and for any
    # request that doesn't pass an explicit `?model=` query param. All three
    # scorers are always constructed (see app/main.py's lifespan) and a
    # caller can pick per-request regardless of this setting - see
    # DataStore.list_assessments/get_assessment's `model` param and
    # GET /models for the full set of choices.
    # "rpt"     -> RPTScorer, real SAP-RPT tabular foundation model calls.
    # "sklearn" -> SklearnScorer, offline-trained scikit-learn GBR+RF models
    #              (see ../train_sklearn_scorer.py).
    # "dummy"   -> DummyScorer, deterministic mock (used by the test suite
    #              and as an explicit opt-out / offline-dev fallback).
    SCORER: str = os.getenv("SCORER", "rpt").strip().lower()

    # Optional override for the *default* scorer's reported model_version
    # (e.g. pinning a specific label in local dev). When unset, each scorer
    # reports its own descriptive default (see app/scorer.py's
    # RPTScorer/SklearnScorer/DummyScorer.model_version class attributes).
    MODEL_VERSION: str | None = os.getenv("MODEL_VERSION") or None

    # --- SAP AI Core / RPT deployment (see repo-root .env) ---
    AICORE_CLIENT_ID: str = os.getenv("AICORE_CLIENT_ID", "")
    AICORE_CLIENT_SECRET: str = os.getenv("AICORE_CLIENT_SECRET", "")
    AICORE_AUTH_URL: str = os.getenv("AICORE_AUTH_URL", "")
    AICORE_RESOURCE_GROUP: str = os.getenv("AICORE_RESOURCE_GROUP", "")
    RPT_URL: str = os.getenv("RPT", "").strip()
    RPT_TIMEOUT_SECONDS: float = float(os.getenv("RPT_TIMEOUT_SECONDS", "20"))
    # Safety buffer subtracted from the OAuth token's expires_in before we
    # consider it stale and fetch a new one, so we never send a request with
    # a token that expires mid-flight.
    RPT_TOKEN_REFRESH_BUFFER_SECONDS: float = float(os.getenv("RPT_TOKEN_REFRESH_BUFFER_SECONDS", "60"))
    # Size of the stratified proxy-labeled context pool sampled once from
    # TRANSACTION_FRAUD_FEATURES.parquet's SPLIT == "train" rows (see
    # RPTScorer._build_context_pool in app/scorer.py).
    RPT_CONTEXT_POOL_SIZE: int = int(os.getenv("RPT_CONTEXT_POOL_SIZE", "200"))

    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

    # --- HANA Cloud connection (see repo-root .env / fraud_data_cleaning.py's
    # connect()) - used only by the independent time-to-resolve prediction
    # feature (app/time_prediction.py + app/routers/time_prediction.py). Not
    # used by the risk-assessment API, which reads pre-cleaned parquet files
    # under CLEANED_DATA_DIR instead. There is no team_03_credentials.json in
    # this repo - plain HANA_* env vars are the only real credential source. ---
    HANA_HOST: str = os.getenv("HANA_HOST", "")
    HANA_PORT: int = int(os.getenv("HANA_PORT", "0") or 0)
    HANA_USER: str = os.getenv("HANA_USER", "")
    HANA_PASSWORD: str = os.getenv("HANA_PASSWORD", "")
    HANA_SCHEMA: str = os.getenv("HANA_SCHEMA", "TEAM_03")

    # --- Time-to-resolve prediction defaults (see app/time_prediction.py) ---
    TIME_PREDICTION_CONTEXT_ROWS: int = int(os.getenv("TIME_PREDICTION_CONTEXT_ROWS", "50"))
    TIME_PREDICTION_CANDIDATE_ROWS: int = int(os.getenv("TIME_PREDICTION_CANDIDATE_ROWS", "500"))
    # Cap on concurrent RPT /predict calls fired for one analyst's open-case
    # batch (an analyst can have 50+ open cases) - keeps us from blasting the
    # AI Core endpoint with an unbounded number of simultaneous requests.
    TIME_PREDICTION_MAX_CONCURRENCY: int = int(os.getenv("TIME_PREDICTION_MAX_CONCURRENCY", "10"))


settings = Settings()
