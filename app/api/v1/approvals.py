from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.user_context import UserContext, get_user_context
from app.schemas.approvals import PendingApprovalsPageRead
from app.services import access_resolution_service, approvals_service, tenant_scope_service

router = APIRouter(prefix="/approvals", tags=["approvals"])


def _require_approver(db_session: Session, ctx: UserContext, org_id) -> None:
    if not access_resolution_service.user_may_list_org_approval_requests(db_session, ctx, org_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient access to view pending approvals.",
        )


def _require_admin(db_session: Session, ctx: UserContext, org_id) -> None:
    if not access_resolution_service.user_may_admin_view_approval_requests(db_session, ctx, org_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required to view the org-wide approval queue.",
        )


@router.get("/pending", response_model=PendingApprovalsPageRead)
def list_pending_approvals(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    include_previews: bool = Query(
        False,
        description="If true, runs preflight/dry-run for supported S3 recommendation types (slower).",
    ),
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> PendingApprovalsPageRead:
    org_id = tenant_scope_service.require_data_access_organization_id(db_session, ctx)
    _require_admin(db_session, ctx, org_id)
    return approvals_service.list_pending_approvals_for_organization(
        db_session,
        org_id,
        limit=limit,
        offset=offset,
        include_previews=include_previews,
    )


@router.get("/pending/count", response_model=dict)
def pending_approvals_count(
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> dict:
    org_id = tenant_scope_service.require_data_access_organization_id(db_session, ctx)
    _require_approver(db_session, ctx, org_id)
    n = approvals_service.count_pending_approvals_for_user(db_session, org_id, ctx.user_id)
    return {"count": n}
