"""Org-level notification policy service.

Controls:
- priority filtering (drop events below min_priority)
- event-type allowlist
- throttle window configuration
- digest vs instant mode
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.notification_policy import NotificationPolicy
from app.core.db import utc_now

_PRIORITY_RANK = {"low": 0, "medium": 1, "high": 2}


def get_policy(db: Session, org_id: UUID) -> NotificationPolicy | None:
    return (
        db.query(NotificationPolicy)
        .filter(NotificationPolicy.organization_id == org_id)
        .first()
    )


def get_or_create_policy(db: Session, org_id: UUID) -> NotificationPolicy:
    row = get_policy(db, org_id)
    if row is None:
        row = NotificationPolicy(
            organization_id=org_id,
            min_priority="low",
            enabled_event_types=None,
            throttle_window_minutes=60,
            max_per_window=10,
            digest_mode="instant",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def update_policy(db: Session, org_id: UUID, updates: dict) -> NotificationPolicy:
    row = get_or_create_policy(db, org_id)
    for key, value in updates.items():
        if hasattr(row, key):
            setattr(row, key, value)
    row.updated_at = utc_now()
    db.commit()
    db.refresh(row)
    return row


def passes_priority_filter(policy: NotificationPolicy, priority: str) -> bool:
    """Return True if the event priority meets or exceeds the org's min_priority."""
    event_rank = _PRIORITY_RANK.get(priority, 0)
    min_rank = _PRIORITY_RANK.get(policy.min_priority or "low", 0)
    return event_rank >= min_rank


def passes_event_filter(policy: NotificationPolicy, event_type: str) -> bool:
    """Return True if the event type is allowed by the org policy.

    When enabled_event_types is None or empty, all event types are allowed.
    """
    allowed = policy.enabled_event_types
    if not allowed:
        return True
    return event_type in allowed
