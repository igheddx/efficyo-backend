from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.user_context import UserContext, get_user_context
from app.models.organization import OrgMembership
from app.schemas.copilot import CopilotQueryRequest, CopilotQueryResponse
from app.services import access_resolution_service, cloud_account_service, copilot_service, tenant_scope_service

router = APIRouter(prefix="/copilot", tags=["copilot"])


def _membership_role_for_org(db: Session, ctx: UserContext, organization_id: UUID) -> str:
    if ctx.user_id is None:
        return access_resolution_service.org_membership_role_label(ctx.role)
    mem = (
        db.query(OrgMembership)
        .filter(
            OrgMembership.user_id == ctx.user_id,
            OrgMembership.organization_id == organization_id,
        )
        .first()
    )
    raw = mem.role if mem is not None else "member"
    return access_resolution_service.org_membership_role_label(raw)


@router.post("/query", response_model=CopilotQueryResponse)
def copilot_query_endpoint(
    body: CopilotQueryRequest,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> CopilotQueryResponse:
    """
    Natural-language operations assistant. Uses real platform data only; optional LLM summarizes.
    Does not execute infrastructure changes.
    """
    org_ctx = tenant_scope_service.require_data_access_organization_id(db_session, ctx)
    if org_ctx != body.organization_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="organization_id must match your current organization.",
        )

    tenant_scope_service.require_tenant_accessible(db_session, ctx, body.tenant_id)
    try:
        cloud_account_service.get_cloud_account_or_raise(db_session, body.tenant_id, body.cloud_account_id)
    except ValueError as exc:
        if str(exc) == "cloud_account_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Cloud account not found.",
            ) from exc
        raise

    effective = access_resolution_service.resolve_effective_access(
        db_session, ctx, body.tenant_id, body.cloud_account_id
    )
    if effective == "none":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No operational access to this cloud account for the copilot.",
        )

    mrole = _membership_role_for_org(db_session, ctx, body.organization_id)

    return copilot_service.run_copilot_query(
        db_session,
        organization_id=body.organization_id,
        tenant_id=body.tenant_id,
        cloud_account_id=body.cloud_account_id,
        effective_operational_access=effective,
        org_membership_role=mrole,
        query=body.query.strip(),
    )
