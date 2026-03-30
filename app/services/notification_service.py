"""Create and query in-app notifications (per user, per organization)."""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.db import utc_now
from app.models.access_grant import AccessGrant
from app.models.cloud_account import CloudAccount
from app.models.notification import Notification
from app.models.organization import OrgMembership
from app.models.recommendation import Recommendation
from app.models.tenant import Tenant
from app.models.user import User

logger = logging.getLogger(__name__)

APPROVER_ROLES = frozenset({"approver", "org_admin", "root_admin"})
EXECUTOR_ROLES = frozenset({"admin", "org_admin", "root_admin"})
EXECUTION_AUDIENCE_ROLES = frozenset({"admin", "org_admin", "root_admin", "approver"})


def resolve_notification_user_id(db: Session, user_id: UUID | None, email: str | None) -> UUID | None:
    if user_id is not None:
        return user_id
    if not email or not str(email).strip():
        return None
    row = db.query(User.id).filter(User.email == str(email).strip()).first()
    return row[0] if row else None


def org_id_for_tenant(db: Session, tenant_id: UUID) -> UUID | None:
    row = db.query(Tenant.organization_id).filter(Tenant.id == tenant_id).first()
    if row is None or row[0] is None:
        return None
    return row[0]


def user_ids_approver_audience_for_org(db: Session, organization_id: UUID) -> list[UUID]:
    """Org admins plus anyone with approver/admin access grants in the org (MSP model)."""
    uids: set[UUID] = set(
        user_ids_for_org_roles(
            db, organization_id, frozenset({"org_admin", "root_admin"})
        )
    )
    for (uid,) in (
        db.query(AccessGrant.user_id)
        .filter(
            AccessGrant.organization_id == organization_id,
            AccessGrant.access_role.in_(("approver", "admin")),
        )
        .distinct()
    ):
        if uid is not None:
            uids.add(uid)
    for (uid,) in (
        db.query(OrgMembership.user_id)
        .filter(
            OrgMembership.organization_id == organization_id,
            OrgMembership.user_id.isnot(None),
            OrgMembership.role.in_(("approver", "admin")),
        )
        .distinct()
    ):
        if uid is not None:
            uids.add(uid)
    return list(uids)


def user_ids_execution_audience_for_org(db: Session, organization_id: UUID) -> list[UUID]:
    uids = set(user_ids_approver_audience_for_org(db, organization_id))
    uids.update(user_ids_executor_audience_for_org(db, organization_id))
    return list(uids)


def user_ids_executor_audience_for_org(db: Session, organization_id: UUID) -> list[UUID]:
    uids: set[UUID] = set(
        user_ids_for_org_roles(
            db, organization_id, frozenset({"org_admin", "root_admin"})
        )
    )
    for (uid,) in (
        db.query(AccessGrant.user_id)
        .filter(
            AccessGrant.organization_id == organization_id,
            AccessGrant.access_role == "admin",
        )
        .distinct()
    ):
        if uid is not None:
            uids.add(uid)
    for (uid,) in (
        db.query(OrgMembership.user_id)
        .filter(
            OrgMembership.organization_id == organization_id,
            OrgMembership.user_id.isnot(None),
            OrgMembership.role == "admin",
        )
        .distinct()
    ):
        if uid is not None:
            uids.add(uid)
    return list(uids)


def user_ids_for_org_roles(db: Session, organization_id: UUID, roles: frozenset[str] | None) -> list[UUID]:
    q = db.query(OrgMembership.user_id).filter(
        OrgMembership.organization_id == organization_id,
        OrgMembership.user_id.isnot(None),
    )
    if roles is not None:
        q = q.filter(OrgMembership.role.in_(set(roles)))
    out: set[UUID] = set()
    for (uid,) in q.all():
        if uid is not None:
            out.add(uid)
    return list(out)


def create_notification(
    db: Session,
    *,
    user_id: UUID,
    organization_id: UUID,
    notification_type: str,
    message: str,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> Notification:
    row = Notification(
        user_id=user_id,
        organization_id=organization_id,
        type=notification_type,
        message=message,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload,
        is_read=False,
        created_at=utc_now(),
    )
    db.add(row)
    return row


def emit_to_users(
    db: Session,
    *,
    organization_id: UUID,
    user_ids: list[UUID],
    notification_type: str,
    message: str,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    if not user_ids:
        return
    for uid in user_ids:
        create_notification(
            db,
            user_id=uid,
            organization_id=organization_id,
            notification_type=notification_type,
            message=message,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
        )
    try:
        db.commit()
    except Exception:
        logger.exception("Failed to commit notifications", extra={"type": notification_type})
        db.rollback()


def _cloud_label(db: Session, tenant_id: UUID, cloud_account_id: UUID) -> str:
    ca = (
        db.query(CloudAccount)
        .filter(CloudAccount.tenant_id == tenant_id, CloudAccount.id == cloud_account_id)
        .first()
    )
    if ca is None:
        return str(cloud_account_id)[:8]
    name = ca.name or "Cloud account"
    if ca.account_id:
        return f"{name} ({ca.account_id})"
    return name


def notify_sync_terminal(
    db: Session,
    *,
    tenant_id: UUID,
    cloud_account_id: UUID,
    job_id: UUID,
    success: bool,
    error_message: str | None = None,
) -> None:
    org_id = org_id_for_tenant(db, tenant_id)
    if org_id is None:
        return
    label = _cloud_label(db, tenant_id, cloud_account_id)
    user_ids = user_ids_for_org_roles(db, org_id, None)
    payload = {
        "tenant_id": str(tenant_id),
        "cloud_account_id": str(cloud_account_id),
        "view": "dashboard",
    }
    if success:
        emit_to_users(
            db,
            organization_id=org_id,
            user_ids=user_ids,
            notification_type="sync_completed",
            message=f"Sync finished successfully for {label}.",
            entity_type="ingestion_job",
            entity_id=job_id,
            payload=payload,
        )
    else:
        detail = (error_message or "See sync history for details.")[:500]
        emit_to_users(
            db,
            organization_id=org_id,
            user_ids=user_ids,
            notification_type="sync_failed",
            message=f"Sync failed for {label}: {detail}",
            entity_type="ingestion_job",
            entity_id=job_id,
            payload=payload,
        )


def notify_pending_approvals_after_sync(db: Session, organization_id: UUID, pending_count: int) -> None:
    if pending_count <= 0:
        return
    user_ids = user_ids_approver_audience_for_org(db, organization_id)
    msg = (
        f"{pending_count} recommendation(s) in this organization are waiting for approval."
        if pending_count != 1
        else "1 recommendation in this organization is waiting for approval."
    )
    emit_to_users(
        db,
        organization_id=organization_id,
        user_ids=user_ids,
        notification_type="approval_required",
        message=msg,
        entity_type="organization",
        entity_id=organization_id,
        payload={"view": "approvals"},
    )


def notify_approval_completed(
    db: Session,
    *,
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
    summary: str,
) -> None:
    org_id = org_id_for_tenant(db, tenant_id)
    if org_id is None:
        return
    user_ids = user_ids_executor_audience_for_org(db, org_id)
    payload = {
        "tenant_id": str(tenant_id),
        "cloud_account_id": str(cloud_account_id),
        "recommendation_id": str(recommendation_id),
        "view": "dashboard",
    }
    emit_to_users(
        db,
        organization_id=org_id,
        user_ids=user_ids,
        notification_type="approval_completed",
        message=f"Approved: {summary[:500]}",
        entity_type="recommendation",
        entity_id=recommendation_id,
        payload=payload,
    )


def notify_execution_started(
    db: Session,
    *,
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
    summary: str,
) -> None:
    org_id = org_id_for_tenant(db, tenant_id)
    if org_id is None:
        return
    user_ids = user_ids_execution_audience_for_org(db, org_id)
    payload = {
        "tenant_id": str(tenant_id),
        "cloud_account_id": str(cloud_account_id),
        "recommendation_id": str(recommendation_id),
        "view": "dashboard",
    }
    emit_to_users(
        db,
        organization_id=org_id,
        user_ids=user_ids,
        notification_type="execution_started",
        message=f"Execution started: {summary[:500]}",
        entity_type="recommendation",
        entity_id=recommendation_id,
        payload=payload,
    )


def notify_execution_completed(
    db: Session,
    *,
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
    summary: str,
) -> None:
    org_id = org_id_for_tenant(db, tenant_id)
    if org_id is None:
        return
    user_ids = user_ids_execution_audience_for_org(db, org_id)
    payload = {
        "tenant_id": str(tenant_id),
        "cloud_account_id": str(cloud_account_id),
        "recommendation_id": str(recommendation_id),
        "view": "dashboard",
    }
    emit_to_users(
        db,
        organization_id=org_id,
        user_ids=user_ids,
        notification_type="execution_completed",
        message=f"Execution completed: {summary[:500]}",
        entity_type="recommendation",
        entity_id=recommendation_id,
        payload=payload,
    )


def notify_execution_failed(
    db: Session,
    *,
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
    summary: str,
    detail: str,
) -> None:
    org_id = org_id_for_tenant(db, tenant_id)
    if org_id is None:
        return
    user_ids = user_ids_execution_audience_for_org(db, org_id)
    payload = {
        "tenant_id": str(tenant_id),
        "cloud_account_id": str(cloud_account_id),
        "recommendation_id": str(recommendation_id),
        "view": "dashboard",
    }
    msg = f"Execution failed for {summary[:200]}: {detail[:400]}"
    emit_to_users(
        db,
        organization_id=org_id,
        user_ids=user_ids,
        notification_type="execution_failed",
        message=msg,
        entity_type="recommendation",
        entity_id=recommendation_id,
        payload=payload,
    )


def list_notifications(
    db: Session,
    *,
    user_id: UUID,
    organization_id: UUID,
    read_filter: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Notification], int, int]:
    base = db.query(Notification).filter(
        Notification.user_id == user_id,
        Notification.organization_id == organization_id,
    )
    if read_filter is True:
        base = base.filter(Notification.is_read.is_(True))
    elif read_filter is False:
        base = base.filter(Notification.is_read.is_(False))

    total = base.count()
    unread_base = db.query(func.count(Notification.id)).filter(
        Notification.user_id == user_id,
        Notification.organization_id == organization_id,
        Notification.is_read.is_(False),
    )
    unread_count = int(unread_base.scalar() or 0)

    rows = (
        base.order_by(Notification.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return rows, total, unread_count


def mark_notification_read(
    db: Session,
    *,
    notification_id: UUID,
    user_id: UUID,
    organization_id: UUID,
) -> Notification | None:
    row = (
        db.query(Notification)
        .filter(
            Notification.id == notification_id,
            Notification.user_id == user_id,
            Notification.organization_id == organization_id,
        )
        .first()
    )
    if row is None:
        return None
    row.is_read = True
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def mark_all_read(
    db: Session,
    *,
    user_id: UUID,
    organization_id: UUID,
) -> int:
    q = (
        db.query(Notification)
        .filter(
            Notification.user_id == user_id,
            Notification.organization_id == organization_id,
            Notification.is_read.is_(False),
        )
        .all()
    )
    n = 0
    for row in q:
        row.is_read = True
        db.add(row)
        n += 1
    if n:
        db.commit()
    return n


def recommendation_summary(db: Session, tenant_id: UUID, cloud_account_id: UUID, recommendation_id: UUID) -> str:
    rec = (
        db.query(Recommendation)
        .filter(
            Recommendation.tenant_id == tenant_id,
            Recommendation.cloud_account_id == cloud_account_id,
            Recommendation.id == recommendation_id,
        )
        .first()
    )
    if rec is None:
        return str(recommendation_id)[:8]
    return (rec.summary or rec.recommendation_type or str(recommendation_id))[:500]
