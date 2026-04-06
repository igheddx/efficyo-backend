from __future__ import annotations

from unittest.mock import patch

from app.models.cloud_account import CloudAccount
from app.models.recommendation import Recommendation
from app.models.tenant import Tenant
from app.schemas.org_integration import OrgIntegrationUpsert
from app.services import slack_digest_service


def _create_tenant(client, headers: dict, name: str = "tenant-a") -> str:
    res = client.post("/api/v1/tenants", json={"name": name}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _slack_url() -> str:
    return "https://hooks.slack.com/test/placeholder-webhook-url"


def test_webhook_url_validation_rejects_non_slack_url():
    try:
        OrgIntegrationUpsert(webhook_url="https://example.com/not-slack")
        assert False, "Expected webhook URL validation to fail"
    except Exception as exc:
        assert "hooks.slack.com" in str(exc)


def test_upsert_and_get_slack_integration_api(client, db, dev_org_scope_admin):
    headers = dev_org_scope_admin["headers"]
    org_id = str(dev_org_scope_admin["org"].id)

    put_res = client.put(
        f"/api/v1/orgs/{org_id}/integrations/slack",
        headers=headers,
        json={
            "provider": "slack",
            "is_enabled": True,
            "webhook_url": _slack_url(),
            "channel_name": "#cloud-ops",
        },
    )
    assert put_res.status_code == 200, put_res.text
    row = put_res.json()
    assert row["provider"] == "slack"
    assert row["is_enabled"] is True
    assert row["channel_name"] == "#cloud-ops"
    assert row["webhook_url_masked"].startswith("****")

    get_res = client.get(f"/api/v1/orgs/{org_id}/integrations/slack", headers=headers)
    assert get_res.status_code == 200, get_res.text
    got = get_res.json()
    assert got["provider"] == "slack"
    assert got["channel_name"] == "#cloud-ops"


def test_send_slack_test_message_success(client, db, dev_org_scope_admin):
    headers = dev_org_scope_admin["headers"]
    org_id = str(dev_org_scope_admin["org"].id)

    save_res = client.put(
        f"/api/v1/orgs/{org_id}/integrations/slack",
        headers=headers,
        json={
            "provider": "slack",
            "is_enabled": True,
            "webhook_url": _slack_url(),
            "channel_name": "#cloud-ops",
        },
    )
    assert save_res.status_code == 200, save_res.text

    class _Resp:
        status_code = 200
        text = "ok"

    with patch("app.services.adapters.slack_adapter.httpx.post", return_value=_Resp()):
        test_res = client.post(f"/api/v1/orgs/{org_id}/integrations/slack/test", headers=headers)

    assert test_res.status_code == 200, test_res.text
    body = test_res.json()
    assert body["success"] is True
    assert body["last_delivery_status"] == "ok"


def test_send_slack_test_message_failure(client, db, dev_org_scope_admin):
    headers = dev_org_scope_admin["headers"]
    org_id = str(dev_org_scope_admin["org"].id)

    save_res = client.put(
        f"/api/v1/orgs/{org_id}/integrations/slack",
        headers=headers,
        json={
            "provider": "slack",
            "is_enabled": True,
            "webhook_url": _slack_url(),
            "channel_name": "#cloud-ops",
        },
    )
    assert save_res.status_code == 200, save_res.text

    class _Resp:
        status_code = 400
        text = "invalid_payload"

    with patch("app.services.adapters.slack_adapter.httpx.post", return_value=_Resp()):
        test_res = client.post(f"/api/v1/orgs/{org_id}/integrations/slack/test", headers=headers)

    assert test_res.status_code == 200, test_res.text
    body = test_res.json()
    assert body["success"] is False
    assert body["last_delivery_status"] == "error"


def test_top5_summary_generation_and_digest_send(client, db, dev_org_scope_admin):
    headers = dev_org_scope_admin["headers"]
    org = dev_org_scope_admin["org"]
    org_id = str(org.id)

    tenant = Tenant(name="tenant-digest", organization_id=org.id, status="active")
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

    for i in range(7):
        db.add(
            Recommendation(
                tenant_id=tenant.id,
                cloud_account_id=acct.id,
                finding_id=acct.id,
                resource_id=f"r-{i}",
                resource_type="ec2",
                finding_type="underutilized_instance",
                recommendation_type=f"resize_instance_{i%3}",
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

    items = slack_digest_service.build_top_n_for_org(db, org.id, n=5)
    assert len(items) == 3  # grouped by recommendation_type + account

    save_res = client.put(
        f"/api/v1/orgs/{org_id}/integrations/slack",
        headers=headers,
        json={
            "provider": "slack",
            "is_enabled": True,
            "webhook_url": _slack_url(),
            "channel_name": "#cloud-ops",
        },
    )
    assert save_res.status_code == 200, save_res.text

    class _Resp:
        status_code = 200
        text = "ok"

    with patch("app.services.adapters.slack_adapter.httpx.post", return_value=_Resp()):
        digest_res = client.post(f"/api/v1/orgs/{org_id}/integrations/slack/digest", headers=headers)

    assert digest_res.status_code == 200, digest_res.text
    body = digest_res.json()
    assert body["success"] is True
    assert body["last_delivery_status"] == "ok"
