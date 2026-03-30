from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.user_context import UserContext, get_user_context
from app.models.tenant import Tenant
from app.schemas.cloud_account import ExecutionPlanRead, RecommendationGuideRead
from app.schemas.execution_policy_schema import ExecutionEligibilityRead
from app.services import access_resolution_service, cloud_account_service, execution_eligibility_service, execution_plan_service, recommendation_service, tenant_scope_service

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get("/{recommendation_id}/guide", response_model=RecommendationGuideRead, status_code=status.HTTP_200_OK)
def recommendation_guide_endpoint(
    recommendation_id: UUID,
    db_session: Session = Depends(get_db),
) -> RecommendationGuideRead:
    try:
        guide = recommendation_service.get_recommendation_guide_by_id(db_session, recommendation_id)
    except ValueError as exc:
        if str(exc) == "recommendation_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recommendation not found") from exc
        raise

    return RecommendationGuideRead(**guide)


@router.get(
    "/{recommendation_id}/execution-plan",
    response_model=ExecutionPlanRead,
    status_code=status.HTTP_200_OK,
)
def recommendation_execution_plan_endpoint(
    recommendation_id: UUID,
    db_session: Session = Depends(get_db),
) -> ExecutionPlanRead:
    try:
        plan = execution_plan_service.generate_execution_plan(db_session, recommendation_id)
    except ValueError as exc:
        if str(exc) == "recommendation_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recommendation not found") from exc
        raise

    return ExecutionPlanRead(**plan)


@router.get("/{recommendation_id}/execution-eligibility", response_model=ExecutionEligibilityRead)
def recommendation_execution_eligibility(
    recommendation_id: UUID,
    tenant_id: UUID = Query(..., description="Tenant that owns the recommendation."),
    cloud_account_id: UUID = Query(..., description="Cloud account scope."),
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> ExecutionEligibilityRead:
    org_ctx = tenant_scope_service.require_data_access_organization_id(db_session, ctx)
    tenant_scope_service.require_tenant_accessible(db_session, ctx, tenant_id)
    try:
        cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)
    except ValueError as exc:
        if str(exc) == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found.") from exc
        raise
    eff = access_resolution_service.resolve_effective_access(db_session, ctx, tenant_id, cloud_account_id)
    if eff == "none":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No operational access to this cloud account.",
        )
    tenant_row = db_session.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant_row is None or tenant_row.organization_id != org_ctx:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found.")

    data = execution_eligibility_service.compute_execution_eligibility(
        db_session,
        organization_id=org_ctx,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        recommendation_id=recommendation_id,
    )
    return ExecutionEligibilityRead(**data)

