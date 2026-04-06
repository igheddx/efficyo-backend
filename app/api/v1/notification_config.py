"""Notification configuration API — Phase 4.

Routes:
    GET  /orgs/{org_id}/notification-policy          — read org policy
    PUT  /orgs/{org_id}/notification-policy          — update org policy
    GET  /orgs/{org_id}/notification-schedule        — read digest schedule
    PUT  /orgs/{org_id}/notification-schedule        — create/update digest schedule
    GET  /orgs/{org_id}/notification-snoozes         — list active snoozes
    POST /orgs/{org_id}/notification-snooze          — create snooze
    DELETE /orgs/{org_id}/notification-snooze/{id}   — remove snooze
    GET  /orgs/{org_id}/notification-health          — delivery health summary
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.user_context import UserContext, get_user_context
from app.models.notification_delivery_log import NotificationDeliveryLog
from app.models.org_integration import OrgIntegration
from app.schemas.notification_phase4 import (
    NotificationHealthRead,
    NotificationPolicyPatch,
    NotificationPolicyRead,
    NotificationScheduleRead,
    NotificationScheduleUpsert,
    NotificationSnoozeCreate,
    NotificationSnoozeRead,
)
from app.services import notification_policy_service, notification_snooze_service, notification_schedule_service
from app.services.org_service import get_organization

logger = logging.getLogger(__name__)

router = APIRouter(tags=["notification-config"])


def _require_org_admin(db: Session, ctx: UserContext, org_id: UUID):
    """Only org admins, msp_admins, and root_admins may change notification config."""
    org = get_organization(db, org_id, ctx)
    if ctx.role not in {"org_admin", "msp_admin", "root_admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only org admins can manage notification configuration.",
        )
    return org


def _require_org_read(db: Session, ctx: UserContext, org_id: UUID):
    return get_organization(db, org_id, ctx)


# ── Policy ────────────────────────────────────────────────────────────────────

@router.get(
    "/orgs/{org_id}/notification-policy",
    response_model=NotificationPolicyRead,
    summary="Get notification policy for an org",
)
def get_notification_policy(
    org_id: UUID,
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> NotificationPolicyRead:
    _require_org_read(db, ctx, org_id)
    row = notification_policy_service.get_or_create_policy(db, org_id)
    return NotificationPolicyRead.model_validate(row)


@router.put(
    "/orgs/{org_id}/notification-policy",
    response_model=NotificationPolicyRead,
    summary="Update notification policy for an org",
)
def update_notification_policy(
    org_id: UUID,
    body: NotificationPolicyPatch,
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> NotificationPolicyRead:
    _require_org_admin(db, ctx, org_id)
    updates = body.model_dump(exclude_none=True)
    row = notification_policy_service.update_policy(db, org_id, updates)
    return NotificationPolicyRead.model_validate(row)


# ── Schedule ──────────────────────────────────────────────────────────────────

@router.get(
    "/orgs/{org_id}/notification-schedule",
    response_model=NotificationScheduleRead | None,
    summary="Get digest schedule for an org",
)
def get_notification_schedule(
    org_id: UUID,
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> NotificationScheduleRead | None:
    _require_org_read(db, ctx, org_id)
    row = notification_schedule_service.get_schedule(db, org_id)
    if row is None:
        return None
    return NotificationScheduleRead.model_validate(row)


@router.put(
    "/orgs/{org_id}/notification-schedule",
    response_model=NotificationScheduleRead,
    summary="Create or update digest schedule for an org",
)
def upsert_notification_schedule(
    org_id: UUID,
    body: NotificationScheduleUpsert,
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> NotificationScheduleRead:
    _require_org_admin(db, ctx, org_id)
    row = notification_schedule_service.upsert_schedule(
        db,
        org_id,
        frequency=body.frequency,
        day_of_week=body.day_of_week,
        time_of_day=body.time_of_day,
        timezone_name=body.timezone,
        is_enabled=body.is_enabled,
    )
    return NotificationScheduleRead.model_validate(row)


# ── Snooze ────────────────────────────────────────────────────────────────────

@router.get(
    "/orgs/{org_id}/notification-snoozes",
    response_model=list[NotificationSnoozeRead],
    summary="List active snoozes for an org",
)
def list_notification_snoozes(
    org_id: UUID,
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> list[NotificationSnoozeRead]:
    _require_org_read(db, ctx, org_id)
    rows = notification_snooze_service.list_snoozes(db, org_id)
    return [NotificationSnoozeRead.model_validate(r) for r in rows]


@router.post(
    "/orgs/{org_id}/notification-snooze",
    response_model=NotificationSnoozeRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a snooze for a specific event type or item",
)
def create_notification_snooze(
    org_id: UUID,
    body: NotificationSnoozeCreate,
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> NotificationSnoozeRead:
    _require_org_admin(db, ctx, org_id)

    # Resolve created_by from context
    created_by: UUID | None = None
    from app.services import auth_service
    user = auth_service.get_user_by_email(db, ctx.email) if ctx.email else None
    if user:
        created_by = user.id

    row = notification_snooze_service.create_snooze(
        db,
        org_id=org_id,
        entity_key=body.entity_key,
        snooze_until=body.snooze_until,
        user_id=body.user_id,
        reason=body.reason,
        created_by_user_id=created_by,
    )
    return NotificationSnoozeRead.model_validate(row)


@router.delete(
    "/orgs/{org_id}/notification-snooze/{snooze_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a snooze",
)
def delete_notification_snooze(
    org_id: UUID,
    snooze_id: UUID,
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> None:
    _require_org_admin(db, ctx, org_id)
    deleted = notification_snooze_service.delete_snooze(db, snooze_id, org_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snooze not found.")


# ── Health ────────────────────────────────────────────────────────────────────

@router.get(
    "/orgs/{org_id}/notification-health",
    response_model=NotificationHealthRead,
    summary="Delivery health summary for an org",
)
def get_notification_health(
    org_id: UUID,
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> NotificationHealthRead:
    _require_org_read(db, ctx, org_id)

    cutoff_24h = datetime.now(tz=timezone.utc) - timedelta(hours=24)

    logs_24h = (
        db.query(NotificationDeliveryLog)
        .filter(
            NotificationDeliveryLog.organization_id == org_id,
            NotificationDeliveryLog.created_at >= cutoff_24h,
        )
        .all()
    )

    delivered = [l for l in logs_24h if l.status == "delivered"]
    failed = [l for l in logs_24h if l.status in {"failed", "permanently_failed"}]

    last_delivery = max((l.created_at for l in delivered), default=None)
    last_failure_log = max(failed, key=lambda l: l.created_at, default=None)

    pending_retries = (
        db.query(NotificationDeliveryLog)
        .filter(
            NotificationDeliveryLog.organization_id == org_id,
            NotificationDeliveryLog.status == "failed",
            NotificationDeliveryLog.next_retry_at.isnot(None),
        )
        .count()
    )

    integrations_rows = (
        db.query(OrgIntegration)
        .filter(OrgIntegration.organization_id == org_id)
        .all()
    )
    integrations_summary = [
        {
            "provider": r.provider,
            "is_enabled": r.is_enabled,
            "last_delivery_status": r.last_delivery_status,
            "last_delivery_error": r.last_delivery_error,
            "last_test_sent_at": r.last_test_sent_at.isoformat() if r.last_test_sent_at else None,
            "last_digest_sent_at": r.last_digest_sent_at.isoformat() if r.last_digest_sent_at else None,
        }
        for r in integrations_rows
    ]

    return NotificationHealthRead(
        organization_id=org_id,
        last_delivery_at=last_delivery,
        last_failure_at=last_failure_log.created_at if last_failure_log else None,
        last_failure_error=last_failure_log.error if last_failure_log else None,
        delivery_count_24h=len(delivered),
        failure_count_24h=len(failed),
        pending_retries=pending_retries,
        integrations=integrations_summary,
    )
