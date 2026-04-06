"""Notification snooze / suppression service."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.notification_snooze import NotificationSnooze
from app.core.db import utc_now


def is_snoozed(
    db: Session,
    org_id: UUID,
    entity_key: str,
    *,
    user_id: UUID | None = None,
) -> bool:
    """Return True if entity_key is currently snoozed for this org (or user).

    Checks both org-wide snoozes (user_id IS NULL) and user-specific snoozes.
    As soon as a matching active snooze exists the function returns True.
    """
    now = datetime.now(tz=timezone.utc)
    q = db.query(NotificationSnooze).filter(
        NotificationSnooze.organization_id == org_id,
        NotificationSnooze.entity_key == entity_key,
        NotificationSnooze.snooze_until > now,
    )
    # Check org-wide first
    org_wide = q.filter(NotificationSnooze.user_id.is_(None)).first()
    if org_wide is not None:
        return True
    # Check user-specific
    if user_id is not None:
        user_snooze = q.filter(NotificationSnooze.user_id == user_id).first()
        if user_snooze is not None:
            return True
    return False


def create_snooze(
    db: Session,
    *,
    org_id: UUID,
    entity_key: str,
    snooze_until: datetime,
    user_id: UUID | None = None,
    reason: str | None = None,
    created_by_user_id: UUID | None = None,
) -> NotificationSnooze:
    row = NotificationSnooze(
        organization_id=org_id,
        user_id=user_id,
        entity_key=entity_key,
        snooze_until=snooze_until,
        reason=reason,
        created_by_user_id=created_by_user_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def delete_snooze(db: Session, snooze_id: UUID, org_id: UUID) -> bool:
    row = (
        db.query(NotificationSnooze)
        .filter(
            NotificationSnooze.id == snooze_id,
            NotificationSnooze.organization_id == org_id,
        )
        .first()
    )
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def list_snoozes(db: Session, org_id: UUID, *, include_expired: bool = False) -> list[NotificationSnooze]:
    q = db.query(NotificationSnooze).filter(NotificationSnooze.organization_id == org_id)
    if not include_expired:
        now = datetime.now(tz=timezone.utc)
        q = q.filter(NotificationSnooze.snooze_until > now)
    return q.order_by(NotificationSnooze.snooze_until.asc()).all()


def expire_old_snoozes(db: Session) -> int:
    """Delete snooze records that have already passed – called by the digest worker for cleanup."""
    now = datetime.now(tz=timezone.utc)
    rows = (
        db.query(NotificationSnooze)
        .filter(NotificationSnooze.snooze_until <= now)
        .all()
    )
    for r in rows:
        db.delete(r)
    if rows:
        db.commit()
    return len(rows)
