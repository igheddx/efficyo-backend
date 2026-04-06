"""Notification retry service — exponential-backoff retry for failed deliveries.

Retry policy:
  Attempt 1 → 30 s
  Attempt 2 → 2 min (120 s)
  Attempt 3 → 10 min (600 s)
  After 3 retries the log is marked permanently failed and not retried again.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.db import utc_now
from app.models.notification_delivery_log import NotificationDeliveryLog
from app.models.org_integration import OrgIntegration
from app.services.adapters import ADAPTER_REGISTRY
from app.services.notification_formatter import FormattedMessage

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
_BACKOFF_SECONDS = [30, 120, 600]  # per retry attempt (0-indexed)


def backoff_seconds(attempt: int) -> int:
    """Return delay in seconds for the given retry attempt (0-indexed)."""
    if attempt < len(_BACKOFF_SECONDS):
        return _BACKOFF_SECONDS[attempt]
    return _BACKOFF_SECONDS[-1]


def schedule_retry(db: Session, log: NotificationDeliveryLog) -> bool:
    """Mark a failed delivery log for retry if under MAX_RETRIES.

    Returns True if retry was scheduled, False if max retries exceeded.
    """
    if log.retry_count >= MAX_RETRIES:
        log.status = "permanently_failed"
        db.commit()
        return False

    delay = backoff_seconds(log.retry_count)
    log.next_retry_at = datetime.now(tz=timezone.utc) + timedelta(seconds=delay)
    db.commit()
    return True


def get_retryable_logs(db: Session) -> list[NotificationDeliveryLog]:
    """Return failed delivery logs whose retry window has arrived."""
    now = datetime.now(tz=timezone.utc)
    return (
        db.query(NotificationDeliveryLog)
        .filter(
            NotificationDeliveryLog.status == "failed",
            NotificationDeliveryLog.next_retry_at.isnot(None),
            NotificationDeliveryLog.next_retry_at <= now,
            NotificationDeliveryLog.retry_count < MAX_RETRIES,
        )
        .order_by(NotificationDeliveryLog.next_retry_at.asc())
        .limit(50)
        .all()
    )


def execute_retry(
    db: Session,
    log: NotificationDeliveryLog,
    formatted: FormattedMessage,
) -> bool:
    """Re-attempt a single delivery.  Updates log in place.  Returns success flag."""
    integration = (
        db.query(OrgIntegration)
        .filter(
            OrgIntegration.organization_id == log.organization_id,
            OrgIntegration.provider == log.provider,
            OrgIntegration.is_enabled.is_(True),
        )
        .first()
    )
    if integration is None:
        log.status = "permanently_failed"
        log.error = "Integration removed or disabled before retry"
        log.next_retry_at = None
        db.commit()
        return False

    adapter = ADAPTER_REGISTRY.get(log.provider)
    if adapter is None:
        log.status = "permanently_failed"
        log.error = f"No adapter for provider '{log.provider}'"
        log.next_retry_at = None
        db.commit()
        return False

    log.retry_count += 1
    log.next_retry_at = None

    try:
        result = adapter.send(formatted, integration)
    except Exception as exc:
        logger.exception("retry: unhandled error from %s adapter", log.provider)
        result_success = False
        result_error = f"Adapter raised: {exc}"
    else:
        result_success = result.success
        result_error = result.error

    if result_success:
        log.status = "delivered"
        log.error = None
        logger.info(
            "retry: delivery succeeded on attempt %d for log=%s provider=%s",
            log.retry_count,
            log.id,
            log.provider,
        )
    else:
        log.error = result_error
        if log.retry_count < MAX_RETRIES:
            delay = backoff_seconds(log.retry_count)
            log.next_retry_at = datetime.now(tz=timezone.utc) + timedelta(seconds=delay)
            log.status = "failed"
        else:
            log.status = "permanently_failed"
        logger.warning(
            "retry: delivery failed on attempt %d for log=%s: %s",
            log.retry_count,
            log.id,
            result_error,
        )

    db.commit()
    return result_success
