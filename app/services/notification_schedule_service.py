"""Digest schedule service — CRUD + "due now" detection + next_run_at computation."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.db import utc_now
from app.models.notification_schedule import NotificationSchedule

logger = logging.getLogger(__name__)


def get_schedule(db: Session, org_id: UUID) -> NotificationSchedule | None:
    return (
        db.query(NotificationSchedule)
        .filter(NotificationSchedule.organization_id == org_id)
        .first()
    )


def upsert_schedule(
    db: Session,
    org_id: UUID,
    *,
    frequency: str = "daily",
    day_of_week: int | None = None,
    time_of_day: str = "09:00",
    timezone_name: str = "UTC",
    is_enabled: bool = True,
) -> NotificationSchedule:
    row = get_schedule(db, org_id)
    now = utc_now()
    if row is None:
        row = NotificationSchedule(
            organization_id=org_id,
            frequency=frequency,
            day_of_week=day_of_week,
            time_of_day=time_of_day,
            timezone=timezone_name,
            is_enabled=is_enabled,
        )
        db.add(row)
    else:
        row.frequency = frequency
        row.day_of_week = day_of_week
        row.time_of_day = time_of_day
        row.timezone = timezone_name
        row.is_enabled = is_enabled
        row.updated_at = now

    # Recompute next_run_at whenever config changes
    row.next_run_at = compute_next_run(
        frequency=frequency,
        day_of_week=day_of_week,
        time_of_day=time_of_day,
        after=now,
    )
    db.commit()
    db.refresh(row)
    return row


def get_due_schedules(db: Session) -> list[NotificationSchedule]:
    """Return all enabled schedules whose next_run_at is in the past."""
    now = utc_now()
    return (
        db.query(NotificationSchedule)
        .filter(
            NotificationSchedule.is_enabled.is_(True),
            NotificationSchedule.next_run_at <= now,
            NotificationSchedule.next_run_at.isnot(None),
        )
        .all()
    )


def mark_run(db: Session, schedule: NotificationSchedule) -> None:
    """Record a successful run and compute the next scheduled time."""
    now = utc_now()
    schedule.last_run_at = now
    schedule.next_run_at = compute_next_run(
        frequency=schedule.frequency,
        day_of_week=schedule.day_of_week,
        time_of_day=schedule.time_of_day,
        after=now,
    )
    db.commit()


def compute_next_run(
    *,
    frequency: str,
    day_of_week: int | None,
    time_of_day: str,
    after: datetime,
) -> datetime:
    """Compute the next UTC datetime the digest should fire.

    All computation is done in UTC. The stored time_of_day is treated as UTC
    for simplicity (the timezone field is surfaced to the UI but schedule
    execution runs in UTC to keep the database-level comparison simple).
    """
    hour, minute = _parse_time(time_of_day)
    # Candidate = today at time_of_day
    base = after.replace(hour=hour, minute=minute, second=0, microsecond=0, tzinfo=timezone.utc)

    if frequency == "daily":
        # If the time today has already passed, schedule for tomorrow
        if base <= after:
            base += timedelta(days=1)
        return base

    # Weekly
    target_dow = day_of_week if day_of_week is not None else 0  # default Monday
    current_dow = base.weekday()  # 0=Mon
    days_ahead = (target_dow - current_dow) % 7
    if days_ahead == 0 and base <= after:
        days_ahead = 7
    return base + timedelta(days=days_ahead)


def _parse_time(time_of_day: str) -> tuple[int, int]:
    try:
        h, m = time_of_day.split(":")
        return int(h), int(m)
    except Exception:
        return 9, 0
