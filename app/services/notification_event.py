"""Unified notification event model for MEEZI's multi-channel notification system.

Supported event types (Phase 2):
  top_actions        — top-N recommendations digest
  critical_alert     — urgent finding requiring immediate attention
  approval_pending   — one or more pending approval requests
  execution_failed   — an automated action failed during execution
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from typing import Any
from uuid import UUID


class EventType(str, Enum):
    top_actions = "top_actions"
    critical_alert = "critical_alert"
    approval_pending = "approval_pending"
    execution_failed = "execution_failed"


class NotificationPriority(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


@dataclass
class NotificationTargets:
    """Routing hint carried with an event.

    type="org"  -> org channel routing baseline
    type="user" -> direct user routing baseline using user_ids
    """

    type: Literal["org", "user"] = "org"
    user_ids: list[UUID] = field(default_factory=list)


@dataclass
class NotificationEvent:
    """A single deliverable notification event.

    Attributes:
        event_type:  What happened (drives formatting).
        org_id:      Organization UUID — used to look up integrations.
        org_name:    Human-readable org name for message content.
        payload:     Structured data for the event (see below).
        priority:    Caller-supplied urgency hint used in messages.
        created_at:  UTC timestamp of event creation.

    Payload shapes by event_type
    ----------------------------
    top_actions:
        {
            "items": [
                {"title": str, "count": int, "impact": str,
                 "reason": str, "account_name": str,
                 "estimated_savings": float, "link": str}
            ],
            "app_url": str | None,
        }

    critical_alert:
        {
            "alert_title": str,
            "description": str,
            "affected_resource": str,
            "account_name": str,
        }

    approval_pending:
        {
            "approval_title": str,
            "requested_by": str,
            "action_count": int,
            "app_url": str | None,
        }

    execution_failed:
        {
            "action_title": str,
            "error_message": str,
            "resource_id": str,
        }
    """

    event_type: EventType
    org_id: UUID
    org_name: str
    payload: dict[str, Any]
    targets: NotificationTargets = field(default_factory=NotificationTargets)
    priority: NotificationPriority = NotificationPriority.medium
    created_at: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
