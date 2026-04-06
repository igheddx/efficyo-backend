from __future__ import annotations

from unittest.mock import patch

from app.models.cloud_account import CloudAccount
from app.models.recommendation import Recommendation
from app.models.tenant import Tenant
from app.services.notification_dispatcher import dispatch
from app.services.notification_event import EventType, NotificationEvent
from app.services.notification_formatter import format_event


def _slack_url() -> str:
    return "https://hooks.slack.com/test/placeholder-webhook-url"


def _teams_url() -> str:
    return "https://outlook.office.com/webhook/00000000-0000-0000-0000-000000000000@00000000-0000-0000-0000-000000000000/IncomingWebhook/abc123/def456"


def _seed_digest_data(db, org_id):
    tenant = Tenant(name="tenant-fwk", organization_id=org_id, status="active")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    acct = CloudAccount(
        tenant_id=tenant.id,
        account_id="123456789012",
        name="Prod Account",
        role_arn="arn:aws:iam::123456789012:role/FptNextReadOnlyRole",
        external_id="ext-123",
        region_default="us-east-1",
        status="active",
        connection_status="valid",
    )
    db.add(acct)
    db.commit()
    db.refresh(acct)

    for i in range(3):
        db.add(
            Recommendation(
                tenant_id=tenant.id,
                cloud_account_id=acct.id,
                finding_id=acct.id,
                resource_id=f"r-{i}",
                resource_type="ec2",
                finding_type="underutilized_instance",
                recommendation_type=f"resize_instance_{i}",
                recommendation_category="cost",
                summary=f"Resize EC2 set {i}",
                explanation="Lower cost by rightsizing",
                risk_level="medium",
                impact_score="high",
                recommended_action="Resize instance",
                confidence_score="high",
                estimated_savings=100 + i,
                state="active",
            )
        )
    db.commit()


def test_teams_integration_send_test_and_digest(client, db, dev_org_scope_admin):
    headers = dev_org_scope_admin["headers"]
    org = dev_org_scope_admin["org"]
    org_id = str(org.id)

    _seed_digest_data(db, org.id)

    put_res = client.put(
        f"/api/v1/orgs/{org_id}/integrations/teams",
        headers=headers,
        json={"is_enabled": True, "webhook_url": _teams_url(), "channel_name": "Cloud Ops"},
    )
    assert put_res.status_code == 200, put_res.text
    assert put_res.json()["provider"] == "teams"

    class _Resp:
        status_code = 200
        text = "1"

    with patch("app.services.adapters.teams_adapter.httpx.post", return_value=_Resp()):
        test_res = client.post(f"/api/v1/orgs/{org_id}/integrations/teams/test", headers=headers)
        digest_res = client.post(f"/api/v1/orgs/{org_id}/integrations/teams/digest", headers=headers)

    assert test_res.status_code == 200, test_res.text
    assert test_res.json()["success"] is True
    assert digest_res.status_code == 200, digest_res.text
    assert digest_res.json()["success"] is True


def test_telegram_integration_send_test_and_digest(client, db, dev_org_scope_admin):
    headers = dev_org_scope_admin["headers"]
    org = dev_org_scope_admin["org"]
    org_id = str(org.id)

    _seed_digest_data(db, org.id)

    put_res = client.put(
        f"/api/v1/orgs/{org_id}/integrations/telegram",
        headers=headers,
        json={
            "is_enabled": True,
            "bot_token": "123456:ABCDEF_TEST_TOKEN",
            "chat_id": "-1001234567890",
            "channel_name": "Ops Alerts",
        },
    )
    assert put_res.status_code == 200, put_res.text
    row = put_res.json()
    assert row["provider"] == "telegram"
    assert row["bot_token_masked"].startswith("****")

    class _Resp:
        status_code = 200
        headers = {"content-type": "application/json"}

        def json(self):
            return {"ok": True}

        text = "{\"ok\": true}"

    with patch("app.services.adapters.telegram_adapter.httpx.post", return_value=_Resp()):
        test_res = client.post(f"/api/v1/orgs/{org_id}/integrations/telegram/test", headers=headers)
        digest_res = client.post(f"/api/v1/orgs/{org_id}/integrations/telegram/digest", headers=headers)

    assert test_res.status_code == 200, test_res.text
    assert test_res.json()["success"] is True
    assert digest_res.status_code == 200, digest_res.text
    assert digest_res.json()["success"] is True


def test_formatter_consistency_top_actions(dev_org_scope_admin):
    org = dev_org_scope_admin["org"]
    event = NotificationEvent(
        event_type=EventType.top_actions,
        org_id=org.id,
        org_name=org.name,
        payload={
            "items": [
                {
                    "title": "Resize instances",
                    "count": 4,
                    "impact": "High",
                    "reason": "Consistent low CPU usage",
                    "account_name": "Prod",
                }
            ],
            "app_url": "https://app.meezi.io",
        },
    )
    msg = format_event(event)
    assert "Top 1" in msg.title
    assert len(msg.lines) == 1
    assert "Resize instances" in msg.lines[0]
    assert "Organization:" in msg.footer


def test_dispatcher_failure_isolation(db, dev_org_scope_admin):
    from app.services import slack_digest_service

    org = dev_org_scope_admin["org"]

    slack = slack_digest_service.upsert_integration(
        db,
        org.id,
        provider="slack",
        is_enabled=True,
        webhook_url=_slack_url(),
        channel_name="#cloud-ops",
    )
    teams = slack_digest_service.upsert_integration(
        db,
        org.id,
        provider="teams",
        is_enabled=True,
        webhook_url=_teams_url(),
        channel_name="Cloud Ops",
    )

    event = NotificationEvent(
        event_type=EventType.top_actions,
        org_id=org.id,
        org_name=org.name,
        payload={"items": [{"title": "A"}], "app_url": None},
    )

    class _SlackFailResp:
        status_code = 400
        text = "invalid_payload"

    class _TeamsOkResp:
        status_code = 200
        text = "1"

    with patch("app.services.adapters.slack_adapter.httpx.post", return_value=_SlackFailResp()), patch(
        "app.services.adapters.teams_adapter.httpx.post", return_value=_TeamsOkResp()
    ):
        results = dispatch(db, event)

    assert results["slack"].success is False
    assert results["teams"].success is True

    db.refresh(slack)
    db.refresh(teams)
    assert slack.last_delivery_status == "error"
    assert teams.last_delivery_status == "ok"
