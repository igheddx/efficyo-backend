from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.user_context import UserContext, get_user_context
from app.schemas.notification import (
    MarkAllReadResponse,
    MarkReadResponse,
    NotificationRead,
    NotificationsPageRead,
)
from app.services import notification_service, tenant_scope_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _require_notification_user(db: Session, ctx: UserContext) -> UUID:
    uid = notification_service.resolve_notification_user_id(db, ctx.user_id, ctx.email)
    if uid is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Notifications require a resolved user identity.",
        )
    return uid


@router.get("", response_model=NotificationsPageRead)
def list_notifications(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    read: str | None = Query(
        None,
        description="Filter: 'true' (read only), 'false' (unread only), omit for all.",
    ),
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> NotificationsPageRead:
    user_id = _require_notification_user(db_session, ctx)
    org_id = tenant_scope_service.require_data_access_organization_id(db_session, ctx)

    read_filter: bool | None = None
    if read is not None:
        v = read.strip().lower()
        if v == "true":
            read_filter = True
        elif v == "false":
            read_filter = False
        elif v:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid read filter. Use 'true', 'false', or omit.",
            )

    rows, total, unread_count = notification_service.list_notifications(
        db_session,
        user_id=user_id,
        organization_id=org_id,
        read_filter=read_filter,
        limit=limit,
        offset=offset,
    )
    return NotificationsPageRead(
        items=[NotificationRead.model_validate(r) for r in rows],
        total=total,
        unread_count=unread_count,
    )


@router.post("/{notification_id}/read", response_model=MarkReadResponse)
def mark_notification_read_endpoint(
    notification_id: UUID,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> MarkReadResponse:
    user_id = _require_notification_user(db_session, ctx)
    org_id = tenant_scope_service.require_data_access_organization_id(db_session, ctx)
    row = notification_service.mark_notification_read(
        db_session,
        notification_id=notification_id,
        user_id=user_id,
        organization_id=org_id,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found.")
    return MarkReadResponse(id=row.id, is_read=row.is_read)


@router.post("/read-all", response_model=MarkAllReadResponse)
def mark_all_notifications_read(
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> MarkAllReadResponse:
    user_id = _require_notification_user(db_session, ctx)
    org_id = tenant_scope_service.require_data_access_organization_id(db_session, ctx)
    n = notification_service.mark_all_read(
        db_session,
        user_id=user_id,
        organization_id=org_id,
    )
    return MarkAllReadResponse(updated=n)
