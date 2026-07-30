"""Pydantic v2 models mirroring risk_assessment_schema.json and
risk_assessment_stream_event_schema.json. These exist for FastAPI's
request/response typing + OpenAPI docs; the jsonschema-based validation in
schema_validation.py is the source of truth for contract conformance (see
that module + tests/test_schema_conformance.py).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RiskTier = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
TypologyCategory = Literal[
    "placement", "layering", "structure", "geography",
    "behavioural_deviation", "sanctions", "predicate_offence",
]
ReviewStatus = Literal["PENDING", "IN_REVIEW", "ESCALATED", "CLEARED", "SAR_FILED"]
EventType = Literal["ASSESSMENT_CREATED", "ASSESSMENT_UPDATED", "REVIEW_STATUS_CHANGED"]


class ComponentScores(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_risk_score: float | None = Field(default=None, ge=0, le=100)
    frequency_risk_score: float | None = Field(default=None, ge=0, le=100)
    geography_risk_score: float | None = Field(default=None, ge=0, le=100)
    counterparty_risk_score: float | None = Field(default=None, ge=0, le=100)
    pattern_risk_score: float | None = Field(default=None, ge=0, le=100)
    velocity_risk_score: float | None = Field(default=None, ge=0, le=100)


class Risk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall_risk_score: float = Field(ge=0, le=100)
    risk_tier: RiskTier
    model_confidence: float = Field(ge=0, le=1)
    is_anomaly: bool
    anomaly_type: str | None = None
    component_scores: ComponentScores | None = None


class TypologySignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^T(0[1-9]|1[0-3])$")
    name: str
    category: TypologyCategory
    triggered: bool
    points: int = Field(ge=0, le=40)
    narrative: str | None = None


class Exposure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    typology_strength_points: int = Field(ge=0, le=100)
    exposure_factor: float = Field(ge=0, le=20)


class ColumnScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str
    contribution_score: float


class ContextRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference_id: str
    similarity_score: float = Field(ge=0, le=1)
    outcome: str | None = None


class Explanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top_column_scores: list[ColumnScore] | None = None
    top_relevant_context_rows: list[ContextRow] | None = None
    narrative_text: str | None = None


class Governance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requires_human_review: Literal[True] = True
    review_status: ReviewStatus
    reviewed_by: str | None = None


class RiskAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: int
    transaction_uuid: str
    scored_at: datetime
    model_version: str
    risk: Risk
    typology_signals: list[TypologySignal]
    exposure: Exposure
    explanation: Explanation
    governance: Governance


class PaginatedAssessments(BaseModel):
    """List-endpoint envelope. Only `items[*]` is required to validate
    against risk_assessment_schema.json - this wrapper itself is not part of
    that contract."""

    items: list[RiskAssessment]
    total: int
    page: int
    page_size: int
    sort: str


class StreamEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: EventType
    sequence: int = Field(ge=0)
    emitted_at: datetime
    assessment: RiskAssessment


class ReviewUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_status: ReviewStatus
    reviewed_by: str | None = None
