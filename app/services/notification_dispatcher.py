"""Notification dispatcher.

Phase 2 behavior remains available:
    - dispatch(db, event)            -> org integrations only
    - dispatch_single(db, event, row)-> one integration only
    - dispatch_test(db, row, org)    -> provider ping

Phase 3 adds:
    - dispatch_routed(db, event) -> org + user routing via policy engine,
      user preferences, direct channel mappings, dedupe/rate-limit, and trace logs.
"""

from __future__ import annotations

import logging
from hashlib import sha1
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.notification_delivery_log import NotificationDeliveryLog
from app.models.org_integration import OrgIntegration
from app.services import notification_service
from app.services.adapters import ADAPTER_REGISTRY, DeliveryResult
from app.services.notification_event import NotificationEvent
from app.services.notification_formatter import format_event
from app.services.notification_routing_service import resolve_routing
from app.services.user_notification_destination_service import allows_event, list_destinations_for_users
import app.services.notification_policy_service as _policy_svc
import app.services.notification_snooze_service as _snooze_svc

logger = logging.getLogger(__name__)


# ── Public API ─────────────────────────────────────────────────────────────────

def dispatch(db: Session, event: NotificationEvent) -> dict[str, DeliveryResult]:
    """Dispatch a notification event to ALL enabled integrations for the org.

    Returns:
        Mapping of provider → DeliveryResult for every attempted delivery.
        Returns an empty dict if no enabled integrations exist.
        Never raises — all failures are captured in the returned results.
    """
    integrations = _enabled_integrations(db, event.org_id)
    if not integrations:
        logger.info("dispatcher: no enabled integrations for org=%s", event.org_id)
        return {}

    try:
        formatted = format_event(event)
    except Exception as exc:
        logger.exception("dispatcher: formatter error for event_type=%s", event.event_type)
        error = f"Formatter error: {exc}"
        return {row.provider: DeliveryResult(success=False, error=error) for row in integrations}

    results: dict[str, DeliveryResult] = {}
    for integration in integrations:
        results[integration.provider] = _send_one(db, formatted, integration)

    return results


def dispatch_single(
    db: Session,
    event: NotificationEvent,
    integration: OrgIntegration,
) -> DeliveryResult:
    """Dispatch a notification event to a single specific integration.

    Returns:
        DeliveryResult — never raises.
    """
    try:
        formatted = format_event(event)
    except Exception as exc:
        logger.exception("dispatcher: formatter error for event_type=%s", event.event_type)
        result = DeliveryResult(success=False, error=f"Formatter error: {exc}")
        _record_delivery(db, integration, result)
        return result

    return _send_one(db, formatted, integration)


def dispatch_routed(db: Session, event: NotificationEvent) -> dict[str, dict[str, DeliveryResult]]:
    """Phase 3/4 routed dispatch with org + user fanout.

    Phase 4 additions:
    - Org policy priority filter
    - Org policy event-type allowlist
    - Org-wide snooze check
    - Policy-driven throttle window (replaces hard-coded 60s)
    - Retry scheduling for failed deliveries

    Returns a nested result map:
        {
          "org": {provider: DeliveryResult},
          "users": {"<user_id>:<provider>": DeliveryResult},
        }
    """
    # ── Phase 4: policy + snooze gate ──────────────────────────────────────
    policy = _policy_svc.get_or_create_policy(db, event.org_id)
    if not _policy_svc.passes_priority_filter(policy, event.priority.value):
        logger.info(
            "dispatcher: dropping event_type=%s for org=%s — below min_priority=%s",
            event.event_type.value, event.org_id, policy.min_priority,
        )
        return {"org": {}, "users": {}}

    if not _policy_svc.passes_event_filter(policy, event.event_type.value):
        logger.info(
            "dispatcher: dropping event_type=%s for org=%s — not in enabled_event_types",
            event.event_type.value, event.org_id,
        )
        return {"org": {}, "users": {}}

    if _snooze_svc.is_snoozed(db, event.org_id, event.event_type.value):
        logger.info(
            "dispatcher: dropping event_type=%s for org=%s — snoozed",
            event.event_type.value, event.org_id,
        )
        return {"org": {}, "users": {}}

    throttle_window = policy.throttle_window_minutes * 60  # convert to seconds

    decision = resolve_routing(db, event)
    formatted = format_event(event)
    integrations = _enabled_integrations(db, event.org_id)

    out_org: dict[str, DeliveryResult] = {}
    out_users: dict[str, DeliveryResult] = {}
    sent_keys: set[str] = set()

    # 1) Org sends
    if decision.send_org:
        for integration in integrations:
            dedupe_key = _dedupe_key(event, integration.provider, "org", str(event.org_id), formatted.title)
            key = f"{integration.provider}:org:{event.org_id}"
            if key in sent_keys:
                out_org[integration.provider] = DeliveryResult(success=False, error="Duplicate skipped")
                _log_delivery(db, event, integration.provider, "org", str(event.org_id), "org_channel", "skipped_dedupe", None, dedupe_key)
                continue
            if _is_rate_limited(db, integration.provider, "org", str(event.org_id), dedupe_key, window_seconds=throttle_window):
                out_org[integration.provider] = DeliveryResult(success=False, error="Rate limited")
                _log_delivery(db, event, integration.provider, "org", str(event.org_id), "org_channel", "rate_limited", None, dedupe_key, rate_limited=True)
                continue

            result = _send_one(db, formatted, integration)
            out_org[integration.provider] = result
            sent_keys.add(key)
            log_entry = _log_delivery(
                db,
                event,
                integration.provider,
                "org",
                str(event.org_id),
                "org_channel",
                "delivered" if result.success else "failed",
                result.error,
                dedupe_key,
            )
            # Phase 4: schedule retry on failure
            if not result.success and log_entry is not None:
                from app.services import notification_retry_service as _retry_svc
                _retry_svc.schedule_retry(db, log_entry)

    # 2) User direct sends + fallback to org
    if decision.user_ids:
        dest_map = list_destinations_for_users(db, organization_id=event.org_id, user_ids=decision.user_ids)
        for uid in decision.user_ids:
            dest = dest_map.get(uid)
            if dest is None or not allows_event(dest, event.event_type.value):
                continue

            # in-app inbox trace notification (user-targeted event receipt)
            n = notification_service.create_notification(
                db,
                user_id=uid,
                organization_id=event.org_id,
                notification_type=event.event_type.value,
                message=formatted.title,
                payload={"lines": formatted.lines, "footer": formatted.footer},
            )

            for integration in integrations:
                provider = integration.provider
                adapter = ADAPTER_REGISTRY.get(provider)
                if adapter is None:
                    continue

                dedupe_key = _dedupe_key(event, provider, "user", str(uid), formatted.title)
                key = f"{provider}:user:{uid}"
                if key in sent_keys:
                    out_users[f"{uid}:{provider}"] = DeliveryResult(success=False, error="Duplicate skipped")
                    _log_delivery(db, event, provider, "user", str(uid), "direct_user", "skipped_dedupe", None, dedupe_key, user_id=uid, notification_id=n.id)
                    continue
                if _is_rate_limited(db, provider, "user", str(uid), dedupe_key, window_seconds=throttle_window):
                    out_users[f"{uid}:{provider}"] = DeliveryResult(success=False, error="Rate limited")
                    _log_delivery(db, event, provider, "user", str(uid), "direct_user", "rate_limited", None, dedupe_key, rate_limited=True, user_id=uid, notification_id=n.id)
                    continue

                direct = adapter.send_direct(formatted, integration, dest)
                if direct.success:
                    out_users[f"{uid}:{provider}"] = direct
                    sent_keys.add(key)
                    _log_delivery(db, event, provider, "user", str(uid), "direct_user", "delivered", None, dedupe_key, user_id=uid, notification_id=n.id)
                    continue

                # Fallback to org channel when user mapping/direct support is missing
                if not decision.send_org:
                    fb_key = f"{provider}:org:{event.org_id}"
                    fb_dedupe = _dedupe_key(event, provider, "org", str(event.org_id), formatted.title)
                    if fb_key not in sent_keys and not _is_rate_limited(db, provider, "org", str(event.org_id), fb_dedupe):
                        org_result = _send_one(db, formatted, integration)
                        out_org[provider] = org_result
                        sent_keys.add(fb_key)
                        _log_delivery(db, event, provider, "org", str(event.org_id), "fallback_org", "delivered" if org_result.success else "failed", org_result.error, fb_dedupe)

                out_users[f"{uid}:{provider}"] = direct
                _log_delivery(db, event, provider, "user", str(uid), "direct_user", "failed", direct.error, dedupe_key, user_id=uid, notification_id=n.id)

        db.commit()

    return {"org": out_org, "users": out_users}


def dispatch_test(
    db: Session,
    integration: OrgIntegration,
    org_name: str,
) -> DeliveryResult:
    """Send a provider-specific test/ping message to verify configuration.

    Returns:
        DeliveryResult — never raises.
    """
    adapter = ADAPTER_REGISTRY.get(integration.provider)
    if adapter is None:
        result = DeliveryResult(
            success=False,
            error=f"No adapter registered for provider '{integration.provider}'",
        )
        _record_delivery(db, integration, result, is_test=True)
        return result

    try:
        result = adapter.send_test(integration, org_name)
    except Exception as exc:
        logger.exception("dispatcher: unhandled error from %s.send_test", integration.provider)
        result = DeliveryResult(success=False, error=f"Adapter raised: {exc}")

    _record_delivery(db, integration, result, is_test=True)
    return result


# ── Internal helpers ───────────────────────────────────────────────────────────

def _send_one(db: Session, formatted, integration: OrgIntegration) -> DeliveryResult:
    provider = integration.provider
    adapter = ADAPTER_REGISTRY.get(provider)
    if adapter is None:
        logger.warning("dispatcher: no adapter registered for provider=%s", provider)
        result = DeliveryResult(success=False, error=f"No adapter for provider '{provider}'")
        _record_delivery(db, integration, result)
        return result

    try:
        result = adapter.send(formatted, integration)
    except Exception as exc:
        logger.exception("dispatcher: unhandled error from %s adapter", provider)
        result = DeliveryResult(success=False, error=f"Adapter raised: {exc}")

    _record_delivery(db, integration, result)

    if result.success:
        logger.info("dispatcher: delivered via %s for org=%s", provider, integration.organization_id)
    else:
        logger.warning(
            "dispatcher: delivery failed via %s for org=%s: %s",
            provider,
            integration.organization_id,
            result.error,
        )
    return result


def _enabled_integrations(db: Session, org_id) -> list[OrgIntegration]:
    return (
        db.query(OrgIntegration)
        .filter(
            OrgIntegration.organization_id == org_id,
            OrgIntegration.is_enabled.is_(True),
        )
        .all()
    )


def _dedupe_key(event: NotificationEvent, provider: str, target_type: str, target_key: str, title: str) -> str:
    raw = f"{event.event_type.value}|{event.org_id}|{provider}|{target_type}|{target_key}|{title}"
    return sha1(raw.encode("utf-8")).hexdigest()[:40]


def _is_rate_limited(
    db: Session,
    provider: str,
    target_type: str,
    target_key: str,
    dedupe_key: str,
    *,
    window_seconds: int = 60,
) -> bool:
    since = datetime.now(tz=timezone.utc).timestamp() - window_seconds
    rows = (
        db.query(NotificationDeliveryLog)
        .filter(
            NotificationDeliveryLog.provider == provider,
            NotificationDeliveryLog.target_type == target_type,
            NotificationDeliveryLog.target_key == target_key,
            NotificationDeliveryLog.dedupe_key == dedupe_key,
            NotificationDeliveryLog.created_at >= datetime.fromtimestamp(since, tz=timezone.utc),
        )
        .count()
    )
    return rows > 0


def _log_delivery(
    db: Session,
    event: NotificationEvent,
    provider: str,
    target_type: str,
    target_key: str,
    route_kind: str,
    status: str,
    error: str | None,
    dedupe_key: str,
    *,
    rate_limited: bool = False,
    user_id=None,
    notification_id=None,
) -> NotificationDeliveryLog | None:
    row = NotificationDeliveryLog(
        organization_id=event.org_id,
        user_id=user_id,
        notification_id=notification_id,
        event_type=event.event_type.value,
        provider=provider,
        target_type=target_type,
        target_key=target_key,
        route_kind=route_kind,
        status=status,
        error=error,
        dedupe_key=dedupe_key,
        rate_limited=rate_limited,
    )
    db.add(row)
    try:
        db.flush()   # obtain the PK without a full commit so callers can use row.id
    except Exception:
        db.rollback()
        return None
    return row


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
    try:
        db.commit()
        db.refresh(integration)
    except Exception:
        logger.exception(
            "dispatcher: failed to record delivery for integration=%s", integration.id
        )
        db.rollback()
