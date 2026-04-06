from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy.orm import Session

from app.services import notification_service
from app.services.notification_event import NotificationEvent, NotificationPriority


@dataclass
class RoutingDecision:
    send_org: bool
    user_ids: list[UUID] = field(default_factory=list)


def resolve_routing(db: Session, event: NotificationEvent) -> RoutingDecision:
    """Resolve org and user targets from event type + priority + explicit targets."""
    send_org = False
    users: set[UUID] = set()

    # Step 1: event-type routing baseline
    if event.event_type.value == "top_actions":
        send_org = True
    elif event.event_type.value == "approval_pending":
        assigned = event.payload.get("assigned_user_ids") or []
        if assigned:
            users.update(_as_uuids(assigned))
        else:
            users.update(notification_service.user_ids_approver_audience_for_org(db, event.org_id))
    elif event.event_type.value == "execution_failed":
        owner = event.payload.get("owner_user_id")
        owners = event.payload.get("owner_user_ids") or []
        if owner:
            users.update(_as_uuids([owner]))
        users.update(_as_uuids(owners))
    elif event.event_type.value == "critical_alert":
        send_org = True
        related = event.payload.get("user_ids") or []
        if related:
            users.update(_as_uuids(related))
        else:
            users.update(notification_service.user_ids_approver_audience_for_org(db, event.org_id))

    # Step 2: explicit targets override/addition
    if event.targets.type == "user":
        send_org = False
        users = set(_as_uuids(event.targets.user_ids))
    elif event.targets.type == "org" and event.targets.user_ids:
        users.update(_as_uuids(event.targets.user_ids))

    # Step 3: priority policy
    if event.priority == NotificationPriority.high:
        send_org = True
    elif event.priority == NotificationPriority.medium:
        send_org = False
    elif event.priority == NotificationPriority.low:
        send_org = True
        users.clear()

    return RoutingDecision(send_org=send_org, user_ids=list(users))


def _as_uuids(values: list) -> list[UUID]:
    out: list[UUID] = []
    for v in values:
        try:
            out.append(UUID(str(v)))
        except Exception:
            continue
    return out
