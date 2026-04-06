from __future__ import annotations

from unittest.mock import patch

from app.models.notification_delivery_log import NotificationDeliveryLog
from app.services import slack_digest_service
from app.services.notification_dispatcher import dispatch_routed
from app.services.notification_event import EventType, NotificationEvent, NotificationPriority, NotificationTargets


def _slack_url() -> str:
    return "https://hooks.slack.com/test/placeholder-webhook-url"


def _telegram_bot() -> str:
    return "123456:ABCDEF_TEST_TOKEN"


def _fake_telegram_resp_ok():
    class _Resp:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = '{"ok": true}'

        def json(self):
            return {"ok": True}

    return _Resp()


def test_user_notification_mapping_stored_via_me_api(client, dev_org_scope_admin):
    headers = dev_org_scope_admin["headers"]
    org_id = str(dev_org_scope_admin["org"].id)

    patch_res = client.patch(
        "/api/v1/me/notification-destination",
        headers=headers,
        json={
            "organization_id": org_id,
            "slack_user_id": "U12345678",
            "telegram_chat_id": "123456789",
            "receive_direct_notifications": True,
            "receive_approvals": True,
            "receive_failures": False,
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
        },
    )
    assert patch_res.status_code == 200, patch_res.text

    get_res = client.get(
        f"/api/v1/me/notification-destination?organization_id={org_id}",
        headers=headers,
    )
    assert get_res.status_code == 200, get_res.text
    data = get_res.json()
    assert data["slack_user_id"] == "U12345678"
    assert data["telegram_chat_id"] == "123456789"
    assert data["receive_failures"] is False


def test_routing_medium_user_only_no_org(db, dev_org_scope_admin):
    org = dev_org_scope_admin["org"]
    user = dev_org_scope_admin["user"]

    slack_digest_service.upsert_integration(
        db,
        org.id,
        provider="slack",
        is_enabled=True,
        webhook_url=_slack_url(),
    )
    from app.services.user_notification_destination_service import patch_destination

    patch_destination(
        db,
        user_id=user.id,
        organization_id=org.id,
        updates={"slack_user_id": "U12345678", "receive_direct_notifications": True},
    )

    event = NotificationEvent(
        event_type=EventType.approval_pending,
        org_id=org.id,
        org_name=org.name,
        targets=NotificationTargets(type="user", user_ids=[user.id]),
        payload={"approval_title": "Approval needed", "assigned_user_ids": [str(user.id)]},
        priority=NotificationPriority.medium,
    )

    class _SlackResp:
        status_code = 200
        text = "ok"

    with patch("app.services.adapters.slack_adapter.httpx.post", return_value=_SlackResp()):
        out = dispatch_routed(db, event)

    assert out["org"] == {}
    assert out["users"].get(f"{user.id}:slack") is not None
    assert out["users"][f"{user.id}:slack"].success is True


def test_routing_low_org_only(db, dev_org_scope_admin):
    org = dev_org_scope_admin["org"]
    user = dev_org_scope_admin["user"]

    slack_digest_service.upsert_integration(
        db,
        org.id,
        provider="slack",
        is_enabled=True,
        webhook_url=_slack_url(),
    )

    event = NotificationEvent(
        event_type=EventType.execution_failed,
        org_id=org.id,
        org_name=org.name,
        targets=NotificationTargets(type="user", user_ids=[user.id]),
        payload={"action_title": "Run X", "owner_user_id": str(user.id), "error_message": "Boom"},
        priority=NotificationPriority.low,
    )

    class _SlackResp:
        status_code = 200
        text = "ok"

    with patch("app.services.adapters.slack_adapter.httpx.post", return_value=_SlackResp()):
        out = dispatch_routed(db, event)

    assert out["org"].get("slack") is not None
    assert out["org"]["slack"].success is True
    assert out["users"] == {}


def test_direct_telegram_with_fallback_to_org_when_mapping_missing(db, dev_org_scope_admin):
    org = dev_org_scope_admin["org"]
    user = dev_org_scope_admin["user"]

    # Org integrations
    slack_digest_service.upsert_integration(
        db,
        org.id,
        provider="slack",
        is_enabled=True,
        webhook_url=_slack_url(),
    )
    slack_digest_service.upsert_integration(
        db,
        org.id,
        provider="telegram",
        is_enabled=True,
        bot_token=_telegram_bot(),
        chat_id="-1001111111111",
    )

    # User destination only has telegram direct mapping
    from app.services.user_notification_destination_service import patch_destination

    patch_destination(
        db,
        user_id=user.id,
        organization_id=org.id,
        updates={"telegram_chat_id": "123456789", "receive_direct_notifications": True},
    )

    event = NotificationEvent(
        event_type=EventType.critical_alert,
        org_id=org.id,
        org_name=org.name,
        targets=NotificationTargets(type="user", user_ids=[user.id]),
        payload={"alert_title": "Critical issue", "user_ids": [str(user.id)]},
        priority=NotificationPriority.high,
    )

    class _SlackResp:
        status_code = 200
        text = "ok"

    with patch("app.services.adapters.slack_adapter.httpx.post", return_value=_SlackResp()), patch(
        "app.services.adapters.telegram_adapter.httpx.post", return_value=_fake_telegram_resp_ok()
    ):
        out = dispatch_routed(db, event)

    # Telegram direct succeeds
    assert out["users"][f"{user.id}:telegram"].success is True
    # Slack direct mapping missing -> fallback org send exists
    assert out["org"].get("slack") is not None

    logs = db.query(NotificationDeliveryLog).filter(NotificationDeliveryLog.organization_id == org.id).all()
    assert len(logs) > 0
