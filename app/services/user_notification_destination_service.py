from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.organization import OrgMembership
from app.models.user_notification_destination import UserNotificationDestination


def can_user_access_org(db: Session, user_id: UUID, organization_id: UUID, is_root_admin: bool) -> bool:
    if is_root_admin:
        return True
    m = (
        db.query(OrgMembership)
        .filter(
            OrgMembership.user_id == user_id,
            OrgMembership.organization_id == organization_id,
        )
        .first()
    )
    return m is not None


def get_or_create_destination(
    db: Session,
    *,
    user_id: UUID,
    organization_id: UUID,
) -> UserNotificationDestination:
    row = (
        db.query(UserNotificationDestination)
        .filter(
            UserNotificationDestination.user_id == user_id,
            UserNotificationDestination.organization_id == organization_id,
        )
        .first()
    )
    if row is None:
        row = UserNotificationDestination(
            user_id=user_id,
            organization_id=organization_id,
            notifications_enabled=True,
            receive_direct_notifications=True,
            receive_approvals=True,
            receive_failures=True,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def patch_destination(
    db: Session,
    *,
    user_id: UUID,
    organization_id: UUID,
    updates: dict,
) -> UserNotificationDestination:
    row = get_or_create_destination(db, user_id=user_id, organization_id=organization_id)
    for key, val in updates.items():
        if hasattr(row, key):
            setattr(row, key, val)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_destinations_for_users(
    db: Session,
    *,
    organization_id: UUID,
    user_ids: list[UUID],
) -> dict[UUID, UserNotificationDestination]:
    if not user_ids:
        return {}
    rows = (
        db.query(UserNotificationDestination)
        .filter(
            UserNotificationDestination.organization_id == organization_id,
            UserNotificationDestination.user_id.in_(user_ids),
        )
        .all()
    )
    return {r.user_id: r for r in rows}


def in_quiet_hours_utc(start: str | None, end: str | None) -> bool:
    if not start or not end:
        return False
    now = datetime.now(tz=timezone.utc)
    now_m = now.hour * 60 + now.minute

    sh, sm = (int(x) for x in start.split(":"))
    eh, em = (int(x) for x in end.split(":"))
    s = sh * 60 + sm
    e = eh * 60 + em

    if s == e:
        return False
    if s < e:
        return s <= now_m < e
    return now_m >= s or now_m < e


def allows_event(dest: UserNotificationDestination, event_type: str) -> bool:
    if not dest.notifications_enabled:
        return False
    if not dest.receive_direct_notifications:
        return False
    if in_quiet_hours_utc(dest.quiet_hours_start, dest.quiet_hours_end):
        return False
    if event_type == "approval_pending" and not dest.receive_approvals:
        return False
    if event_type == "execution_failed" and not dest.receive_failures:
        return False
    return True
