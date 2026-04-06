from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.user_context import UserContext, get_user_context
from app.schemas.me import (
    AttentionTaskItem,
    AttentionTasksResponse,
    CloudAccountSummary,
    ContextDefaultsPatch,
    CurrentOrganizationUpdate,
    MeRead,
    TenantSummary,
    UserPreferencesPatch,
)
from app.schemas.user_notification_destination import (
    UserNotificationDestinationPatch,
    UserNotificationDestinationRead,
)
from app.services import (
    access_resolution_service,
    attention_tasks_service,
    auth_service,
    cloud_account_service,
    me_service,
    tenant_scope_service,
    tenant_service,
)

router = APIRouter(prefix="/me", tags=["me"])


@router.get("", response_model=MeRead)
def get_me(
    tenant_id: UUID | None = Query(
        None,
        description="With cloud_account_id, resolve effective_access_role for this tenant/account context.",
    ),
    cloud_account_id: UUID | None = Query(None),
    db_session: Session = Depends(get_db),
    user_ctx: UserContext = Depends(get_user_context),
) -> MeRead:
    try:
        auth_service.ensure_local_seed_users(db_session)
    except ProgrammingError:
        db_session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Database schema is out of date. Run from the backend directory: alembic upgrade head"
            ),
        ) from None
    return me_service.build_me_read(
        db_session,
        user_ctx,
        selection_tenant_id=tenant_id,
        selection_cloud_account_id=cloud_account_id,
    )


@router.patch("/current-organization", response_model=MeRead)
def patch_current_organization(
    body: CurrentOrganizationUpdate,
    db_session: Session = Depends(get_db),
    user_ctx: UserContext = Depends(get_user_context),
) -> MeRead:
    return me_service.set_current_organization(db_session, user_ctx, body.organization_id)


@router.get("/context-defaults/tenants", response_model=list[TenantSummary])
def get_me_context_default_tenants(
    organization_id: UUID = Query(
        ...,
        description="Organization whose grant-visible tenants to list for default-context UI.",
    ),
    include_demo: bool = Query(
        False,
        description="When true, ensure demo tenants exist (same as GET /tenants?include_demo).",
    ),
    db_session: Session = Depends(get_db),
    user_ctx: UserContext = Depends(get_user_context),
) -> list[TenantSummary]:
    """Tenants for saved defaults; membership role is resolved for `organization_id`, not only the session org."""
    try:
        auth_service.ensure_local_seed_users(db_session)
    except ProgrammingError:
        db_session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Database schema is out of date. Run from the backend directory: alembic upgrade head"
            ),
        ) from None
    if include_demo:
        tenant_service.ensure_demo_tenants(db_session)
    return me_service.list_context_default_tenants(db_session, user_ctx, organization_id)


@router.get("/context-defaults/cloud-accounts", response_model=list[CloudAccountSummary])
def get_me_context_default_cloud_accounts(
    tenant_id: UUID = Query(..., description="Tenant whose visible cloud accounts to list for default-context UI."),
    db_session: Session = Depends(get_db),
    user_ctx: UserContext = Depends(get_user_context),
) -> list[CloudAccountSummary]:
    """List grant-filtered accounts for a tenant in any org the user may access (session-backed)."""
    try:
        auth_service.ensure_local_seed_users(db_session)
    except ProgrammingError:
        db_session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Database schema is out of date. Run from the backend directory: alembic upgrade head"
            ),
        ) from None
    return me_service.list_context_default_cloud_accounts(db_session, user_ctx, tenant_id)


@router.patch("/context-defaults", response_model=MeRead)
def patch_me_context_defaults(
    body: ContextDefaultsPatch,
    db_session: Session = Depends(get_db),
    user_ctx: UserContext = Depends(get_user_context),
) -> MeRead:
    return me_service.patch_user_context_defaults(db_session, user_ctx, body)


@router.patch("/preferences", response_model=MeRead)
def patch_me_preferences(
    body: UserPreferencesPatch,
    db_session: Session = Depends(get_db),
    user_ctx: UserContext = Depends(get_user_context),
) -> MeRead:
    return me_service.patch_user_preferences(
        db_session, user_ctx, receive_approval_emails=body.receive_approval_emails
    )


@router.get("/notification-destination", response_model=UserNotificationDestinationRead)
def get_my_notification_destination(
    organization_id: UUID = Query(...),
    db_session: Session = Depends(get_db),
    user_ctx: UserContext = Depends(get_user_context),
) -> UserNotificationDestinationRead:
    return me_service.get_my_notification_destination(
        db_session,
        user_ctx,
        organization_id=organization_id,
    )


@router.patch("/notification-destination", response_model=UserNotificationDestinationRead)
def patch_my_notification_destination(
    body: UserNotificationDestinationPatch,
    db_session: Session = Depends(get_db),
    user_ctx: UserContext = Depends(get_user_context),
) -> UserNotificationDestinationRead:
    return me_service.patch_my_notification_destination(db_session, user_ctx, body)


@router.get("/tasks", response_model=AttentionTasksResponse)
def get_me_attention_tasks(
    organization_id: UUID = Query(..., description="Must match your current organization context."),
    tenant_id: UUID = Query(...),
    cloud_account_id: UUID = Query(...),
    limit: int = Query(25, ge=1, le=100),
    db_session: Session = Depends(get_db),
    user_ctx: UserContext = Depends(get_user_context),
) -> AttentionTasksResponse:
    """
    Ranked operational attention items for the selected account (impact, urgency, risk, readiness, staleness).
    Same scoring as Copilot prioritize/blockers context.
    """
    org_ctx = tenant_scope_service.require_data_access_organization_id(db_session, user_ctx)
    if org_ctx != organization_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="organization_id must match your current organization.",
        )
    tenant_scope_service.require_tenant_accessible(db_session, user_ctx, tenant_id)
    try:
        cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)
    except ValueError as exc:
        if str(exc) == "cloud_account_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Cloud account not found.",
            ) from exc
        raise
    effective = access_resolution_service.resolve_effective_access(
        db_session, user_ctx, tenant_id, cloud_account_id
    )
    if effective == "none":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No operational access to this cloud account.",
        )
    rows = attention_tasks_service.score_account_attention_items(
        db_session,
        organization_id,
        tenant_id,
        cloud_account_id,
        limit=limit,
    )
    items = [AttentionTaskItem(**row) for row in rows]
    return AttentionTasksResponse(items=items)
