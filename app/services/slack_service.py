"""Slack delivery — backward-compat wrapper over SlackAdapter.

Phase 1 callers that import ``send_test_message`` / ``send_digest`` /
``DeliveryResult`` from this module continue to work.  New code should use
``notification_dispatcher`` or ``SlackAdapter`` directly.

Note: ``httpx`` is imported at the top-level so that tests patching
``app.services.slack_service.httpx.post`` (pre-Phase-2 mock style) still work.
New tests should mock ``app.services.adapters.slack_adapter.httpx.post``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx  # kept for legacy test mocks  # noqa: F401
from sqlalchemy.orm import Session

from app.models.org_integration import OrgIntegration
from app.services.adapters.base import DeliveryResult  # re-export
from app.services.adapters.slack_adapter import SlackAdapter
from app.services.notification_event import EventType, NotificationEvent
from app.services.notification_formatter import format_event

logger = logging.getLogger(__name__)

_adapter = SlackAdapter()


def send_test_message(
    db: Session,
    integration: OrgIntegration,
    org_name: str,
) -> DeliveryResult:
    """Send a test ping; update last_test_sent_at and delivery metadata."""
    result = _adapter.send_test(integration, org_name)
    _record_delivery(db, integration, result, is_test=True)
    return result


def send_digest(
    db: Session,
    integration: OrgIntegration,
    org_name: str,
    items: list[dict],
    app_url: str | None = None,
) -> DeliveryResult:
    """Send the top-N digest; update delivery metadata."""
    if not items:
        result = DeliveryResult(success=False, error="No items to include in digest.")
        _record_delivery(db, integration, result)
        return result

    event = NotificationEvent(
        event_type=EventType.top_actions,
        org_id=integration.organization_id,
        org_name=org_name,
        payload={"items": items, "app_url": app_url},
    )
    formatted = format_event(event)
    result = _adapter.send(formatted, integration)
    _record_delivery(db, integration, result)
    return result


def _record_delivery(
    db: Session,
    integration: OrgIntegration,
    result: DeliveryResult,
    *,
    is_test: bool = False,
) -> None:
    now = datetime.now(tz=timezone.utc)
    integration.last_delivery_status = "ok" if result.success else "error"
    integration.last_delivery_error = result.error if not result.success else None
    if is_test:
        integration.last_test_sent_at = now
    else:
        integration.last_digest_sent_at = now
    db.commit()
    db.refresh(integration)
