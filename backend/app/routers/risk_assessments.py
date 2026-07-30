from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from ..data_store import DataStore
from ..dependencies import get_connection_manager, get_data_store
from ..schemas import PaginatedAssessments, ReviewUpdateRequest, RiskAssessment
from ..streaming import ConnectionManager

router = APIRouter(prefix="/risk-assessments", tags=["risk-assessments"])

TypologyCategory = Literal[
    "placement", "layering", "structure", "geography",
    "behavioural_deviation", "sanctions", "predicate_offence",
]
RiskTier = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
ReviewStatus = Literal["PENDING", "IN_REVIEW", "ESCALATED", "CLEARED", "SAR_FILED"]
SortField = Literal["severity", "overall_risk_score", "typology_strength_points", "transaction_id"]


@router.get("", response_model=PaginatedAssessments)
def list_risk_assessments(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    sort: SortField = Query("severity", description="Default: severity = max(risk.overall_risk_score, exposure.typology_strength_points)"),
    order: Literal["asc", "desc"] = Query("desc"),
    risk_tier: RiskTier | None = Query(None),
    category: TypologyCategory | None = Query(None, description="Filter to transactions with >=1 triggered typology signal in this category"),
    review_status: ReviewStatus | None = Query(None),
    data_store: DataStore = Depends(get_data_store),
) -> PaginatedAssessments:
    items, total = data_store.list_assessments(
        page=page,
        page_size=page_size,
        sort=sort,
        order=order,
        risk_tier=risk_tier,
        category=category,
        review_status=review_status,
    )
    return PaginatedAssessments(
        items=[RiskAssessment.model_validate(a) for a in items],
        total=total,
        page=page,
        page_size=page_size,
        sort=f"{sort}:{order}",
    )


@router.get("/{transaction_id}", response_model=RiskAssessment)
def get_risk_assessment(transaction_id: int, data_store: DataStore = Depends(get_data_store)) -> RiskAssessment:
    assessment = data_store.get_assessment(transaction_id)
    if assessment is None:
        raise HTTPException(status_code=404, detail=f"transaction_id {transaction_id} not found")
    return RiskAssessment.model_validate(assessment)


@router.patch("/{transaction_id}/review", response_model=RiskAssessment)
async def update_review_status(
    transaction_id: int,
    body: ReviewUpdateRequest,
    data_store: DataStore = Depends(get_data_store),
    manager: ConnectionManager = Depends(get_connection_manager),
) -> RiskAssessment:
    """Analyst updates governance.review_status/reviewed_by. Per
    topfraudandtables.json's governance clause, this is the only way review
    state changes - the backend never auto-transitions review_status.
    Emits a REVIEW_STATUS_CHANGED event (full assessment envelope) to all
    connected /ws/risk-stream clients so dashboards update live."""
    assessment = data_store.update_review(transaction_id, body.review_status, body.reviewed_by)
    if assessment is None:
        raise HTTPException(status_code=404, detail=f"transaction_id {transaction_id} not found")

    event = manager.build_event("REVIEW_STATUS_CHANGED", assessment)
    await manager.broadcast(event)

    return RiskAssessment.model_validate(assessment)
