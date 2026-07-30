"""jsonschema-based contract validation against the two repo-root schema
files. Used by the test suite (source of truth for conformance) and,
optionally, as a dev-mode runtime assertion (see config.VALIDATE_RESPONSES)
so a contract violation fails loudly during development instead of shipping
a malformed response to the frontend.
"""
from __future__ import annotations

import json
from functools import lru_cache

from jsonschema import Draft202012Validator, RefResolver

from .config import settings


def _load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _schemas() -> tuple[dict, dict]:
    risk_schema = _load_json(settings.RISK_ASSESSMENT_SCHEMA_PATH)
    stream_schema = _load_json(settings.STREAM_EVENT_SCHEMA_PATH)
    return risk_schema, stream_schema


@lru_cache(maxsize=1)
def _store() -> dict:
    risk_schema, stream_schema = _schemas()
    return {risk_schema["$id"]: risk_schema, stream_schema["$id"]: stream_schema}


def _assessment_validator() -> Draft202012Validator:
    risk_schema, _ = _schemas()
    resolver = RefResolver.from_schema(risk_schema, store=_store())
    return Draft202012Validator(risk_schema, resolver=resolver)


def _stream_event_validator() -> Draft202012Validator:
    _, stream_schema = _schemas()
    resolver = RefResolver.from_schema(stream_schema, store=_store())
    return Draft202012Validator(stream_schema, resolver=resolver)


def validate_assessment(instance: dict) -> None:
    """Raises jsonschema.exceptions.ValidationError on contract violation."""
    _assessment_validator().validate(instance)


def validate_stream_event(instance: dict) -> None:
    _stream_event_validator().validate(instance)


def assessment_errors(instance: dict) -> list[str]:
    return [str(e) for e in _assessment_validator().iter_errors(instance)]
