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

# Load backend/.env if present (optional - no secrets required for the mock
# scorer). Does not touch the repo-root .env, which belongs to the HANA/AI
# Core scripts.
load_dotenv(BACKEND_DIR / ".env")


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

    MODEL_VERSION: str = os.getenv("MODEL_VERSION", "dummy-mock-v0")

    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))


settings = Settings()
