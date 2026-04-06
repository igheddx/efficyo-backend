"""Digest scheduler worker — runs due digest schedules and retries failed deliveries.

Intended to run as a periodic job (e.g. called from cron or the sync worker loop
every minute). Safe to call concurrently since it uses `last_run_at` + `next_run_at`
to avoid duplicate sends.

Usage (standalone):
    python scripts/run_digest_worker.py

Usage (import):
    from app.workers.digest_scheduler import run_once
    run_once(db)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.services import notification_schedule_service, notification_snooze_service
from app.services import notification_retry_service
from app.services import slack_digest_service
from app.services.notification_dispatcher import dispatch_single
from app.services.notification_event import EventType, NotificationEvent, NotificationPriority
from app.models.notification_delivery_log import NotificationDeliveryLog
from app.models.org_integration import OrgIntegration
from app.services.notification_formatter import format_event

logger = logging.getLogger(__name__)


def run_due_digests(db: Session) -> int:
    """Check digest schedules and fire any that are due.

    Returns the count of digests dispatched.
    """
    due = notification_schedule_service.get_due_schedules(db)
    dispatched = 0

    for schedule in due:
        org_id: UUID = schedule.organization_id
        try:
            _send_digest_for_org(db, org_id)
            notification_schedule_service.mark_run(db, schedule)
            dispatched += 1
            logger.info("digest_scheduler: dispatched digest for org=%s", org_id)
        except Exception:
            logger.exception("digest_scheduler: error dispatching digest for org=%s", org_id)

    return dispatched


def run_pending_retries(db: Session) -> int:
    """Attempt retry delivery for failed logs that are due.

    Returns the count of retries attempted.
    """
    retryable = notification_retry_service.get_retryable_logs(db)
    attempted = 0

    for log in retryable:
        try:
            _retry_log(db, log)
            attempted += 1
        except Exception:
            logger.exception("digest_scheduler: error retrying log=%s", log.id)

    return attempted


def run_once(db: Session) -> dict[str, int]:
    """Single pass: fire due digests, process retries, expire snoozes."""
    digests = run_due_digests(db)
    retries = run_pending_retries(db)
    expired = notification_snooze_service.expire_old_snoozes(db)
    return {"digests": digests, "retries": retries, "snoozes_expired": expired}


# ── Internal ──────────────────────────────────────────────────────────────────

def _send_digest_for_org(db: Session, org_id: UUID) -> None:
    from app.models.organization import Organization
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        logger.warning("digest_scheduler: org=%s not found, skipping", org_id)
        return

    items = slack_digest_service.build_top_n_for_org(db, org_id, n=5)
    if not items:
        logger.info("digest_scheduler: no digest items for org=%s, skipping", org_id)
        return

    event = NotificationEvent(
        event_type=EventType.top_actions,
        org_id=org_id,
        org_name=org.name,
        payload={"items": [_item_to_dict(i) for i in items]},
        priority=NotificationPriority.medium,
    )

    integrations = (
        db.query(OrgIntegration)
        .filter(
            OrgIntegration.organization_id == org_id,
            OrgIntegration.is_enabled.is_(True),
        )
        .all()
    )

    for integration in integrations:
        try:
            dispatch_single(db, event, integration)
        except Exception:
            logger.exception(
                "digest_scheduler: dispatch_single failed for org=%s provider=%s",
                org_id, integration.provider,
            )


def _retry_log(db: Session, log: NotificationDeliveryLog) -> None:
    """Reconstruct a minimal FormattedMessage from the delivery log and retry."""
    from app.services.notification_formatter import FormattedMessage

    # Build a minimal formatted message for retry (text only — no full event replay needed)
    resp = log.provider_response or {}
    formatted = FormattedMessage(
        title=resp.get("title", f"Retry: {log.event_type}"),
        lines=resp.get("lines", []),
        footer=resp.get("footer", ""),
    )
    notification_retry_service.execute_retry(db, log, formatted)


def _item_to_dict(item) -> dict:
    if isinstance(item, dict):
        return item
    return {
        "title": getattr(item, "title", str(item)),
        "count": getattr(item, "count", 0),
        "impact": getattr(item, "impact", ""),
        "reason": getattr(item, "reason", ""),
        "account_name": getattr(item, "account_name", ""),
        "estimated_savings": getattr(item, "estimated_savings", 0.0),
        "link": getattr(item, "link", None),
    }
