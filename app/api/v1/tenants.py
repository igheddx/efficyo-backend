from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.user_context import UserContext, get_user_context
from app.schemas.recommendation_outcome import SavingsProofSummaryRead
from app.schemas.tenant import TenantCreate, TenantRead
from app.services import (
    access_resolution_service,
    recommendation_outcome_service,
    tenant_scope_service,
    tenant_service,
)

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.post("", response_model=TenantRead, status_code=status.HTTP_201_CREATED)
def create_tenant_endpoint(
    body: TenantCreate,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> TenantRead:
    tenant_scope_service.require_tenant_write_role(ctx)
    if body.organization_id is not None:
        if not ctx.is_platform_root:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only platform administrators can set organization_id when creating a customer.",
            )
        tenant_scope_service.assert_organization_accessible(db_session, ctx, body.organization_id)
        org_id = body.organization_id
    else:
        org_id = tenant_scope_service.require_data_access_organization_id(db_session, ctx)
    try:
        tenant = tenant_service.create_tenant(db_session, body.name, organization_id=org_id)
        return TenantRead.model_validate(tenant)
    except IntegrityError as exc:
        db_session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tenant name already exists",
        ) from exc


@router.get("", response_model=list[TenantRead])
def list_tenants_endpoint(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    include_demo: bool = Query(False),
    organization_id: UUID | None = Query(
        None,
        description="When set, list tenants for this organization (caller must be allowed to access it).",
    ),
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> list[TenantRead]:
    if organization_id is not None:
        tenant_scope_service.assert_organization_accessible(db_session, ctx, organization_id)
        org_id = organization_id
    else:
        org_id = tenant_scope_service.require_data_access_organization_id(db_session, ctx)
    if include_demo:
        tenant_service.ensure_demo_tenants(db_session)
    tenants = tenant_service.list_tenants_for_organization(
        db_session, org_id, skip=skip, limit=limit
    )
    visible = [
        t for t in tenants if access_resolution_service.tenant_has_any_account_access(db_session, ctx, t.id)
    ]
    return [TenantRead.model_validate(tenant) for tenant in visible]


@router.get("/{tenant_id}/savings-proof/summary", response_model=SavingsProofSummaryRead)
def tenant_savings_proof_summary_endpoint(
    tenant_id: UUID,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> SavingsProofSummaryRead:
    tenant_scope_service.require_tenant_accessible(db_session, ctx, tenant_id)
    result = recommendation_outcome_service.savings_proof_summary_for_tenant(db_session, tenant_id)
    return SavingsProofSummaryRead(**result)


@router.get("/{tenant_id}", response_model=TenantRead)
def get_tenant_endpoint(
    tenant_id: UUID,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> TenantRead:
    tenant_scope_service.require_tenant_accessible(db_session, ctx, tenant_id)
    tenant = tenant_service.get_tenant(db_session, tenant_id)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )
    return TenantRead.model_validate(tenant)
