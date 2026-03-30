from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.schemas.recommendation_outcome import RecommendationOutcomeRead
from app.services import outcome_impact_service

router = APIRouter(prefix="/outcomes", tags=["outcomes"])


@router.post(
    "/{outcome_id}/re-evaluate",
    response_model=RecommendationOutcomeRead,
    status_code=status.HTTP_200_OK,
)
def re_evaluate_outcome_endpoint(
    outcome_id: UUID,
    db_session: Session = Depends(get_db),
) -> RecommendationOutcomeRead:
    try:
        outcome = outcome_impact_service.re_evaluate_outcome_by_id(
            db_session=db_session,
            outcome_id=outcome_id,
        )
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "outcome_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Outcome not found") from exc
        if error_msg == "outcome_not_actionable":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Outcome must be acted on or verified before re-evaluation",
            ) from exc
        raise

    return RecommendationOutcomeRead.model_validate(outcome)

