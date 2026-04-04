from datetime import datetime, timezone
from uuid import uuid4

from app.models.cloud_account import CloudAccount
from app.models.finding import Finding
from app.models.resource_snapshot import ResourceSnapshot
from app.models.tenant import Tenant
from app.services import recommendation_service


def _seed_scope(db, org_id):
    tenant = Tenant(name="api-rec-tenant", status="active", organization_id=org_id)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    account = CloudAccount(
        tenant_id=tenant.id,
        account_id="123456789012",
        name="api-rec-account",
        status="connected",
        role_arn="arn:aws:iam::123456789012:role/fptnext-validator",
        region_default="us-east-1",
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return tenant, account


def _seed_rec(db, tenant_id, account_id):
    snap = ResourceSnapshot(
        tenant_id=tenant_id,
        cloud_account_id=account_id,
        resource_id="fn-api",
        resource_type="lambda_function",
        region="us-east-1",
        configuration_json={"runtime": "python3.6"},
        tags_json={"Name": "fn-api"},
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)

    run_id = uuid4()
    finding = Finding(
        tenant_id=tenant_id,
        cloud_account_id=account_id,
        resource_snapshot_id=snap.id,
        resource_id="fn-api",
        resource_type="lambda_function",
        finding_type="lambda_outdated_runtime",
        severity="medium",
        evidence_json={"runtime": "python3.6"},
        detected_at=datetime.now(timezone.utc),
        sync_run_id=run_id,
    )
    db.add(finding)
    db.commit()
    recommendation_service.generate_rds_recommendations(db, tenant_id, account_id, sync_run_id=run_id)
    return recommendation_service.list_recommendations(db, tenant_id, account_id, latest_only=True, state_view="all")[0]


def test_recommendation_snooze_and_filter_views(client, db, dev_org_scope):
    tenant, account = _seed_scope(db, dev_org_scope["org"].id)
    rec = _seed_rec(db, tenant.id, account.id)

    snooze_res = client.post(
        f"/api/v1/tenants/{tenant.id}/cloud-accounts/{account.id}/recommendations/{rec.id}/snooze",
        json={"days": 7},
        headers=dev_org_scope["headers"],
    )
    assert snooze_res.status_code == 200
    assert snooze_res.json()["state"] == "snoozed"

    active_res = client.get(
        f"/api/v1/tenants/{tenant.id}/cloud-accounts/{account.id}/recommendations?state_view=active",
        headers=dev_org_scope["headers"],
    )
    assert active_res.status_code == 200
    assert active_res.json() == []

    snoozed_res = client.get(
        f"/api/v1/tenants/{tenant.id}/cloud-accounts/{account.id}/recommendations?state_view=snoozed",
        headers=dev_org_scope["headers"],
    )
    assert snoozed_res.status_code == 200
    assert len(snoozed_res.json()) == 1


def test_recommendation_dismiss_and_reactivate(client, db, dev_org_scope):
    tenant, account = _seed_scope(db, dev_org_scope["org"].id)
    rec = _seed_rec(db, tenant.id, account.id)

    dismiss_res = client.post(
        f"/api/v1/tenants/{tenant.id}/cloud-accounts/{account.id}/recommendations/{rec.id}/dismiss",
        json={"reason": "risk_accepted", "note": "Accepted by team"},
        headers=dev_org_scope["headers"],
    )
    assert dismiss_res.status_code == 200
    assert dismiss_res.json()["state"] == "dismissed"

    reactivate_res = client.post(
        f"/api/v1/tenants/{tenant.id}/cloud-accounts/{account.id}/recommendations/{rec.id}/reactivate",
        headers=dev_org_scope["headers"],
    )
    assert reactivate_res.status_code == 200
    assert reactivate_res.json()["state"] == "active"


def test_bulk_state_update_snooze_then_dismiss(client, db, dev_org_scope):
    tenant, account = _seed_scope(db, dev_org_scope["org"].id)
    rec = _seed_rec(db, tenant.id, account.id)

    bulk_snooze = client.post(
        f"/api/v1/tenants/{tenant.id}/cloud-accounts/{account.id}/recommendations/bulk-state",
        json={"recommendation_ids": [str(rec.id)], "action": "snooze", "days": 30},
        headers=dev_org_scope["headers"],
    )
    assert bulk_snooze.status_code == 200
    assert bulk_snooze.json()["updated_count"] == 1

    bulk_dismiss = client.post(
        f"/api/v1/tenants/{tenant.id}/cloud-accounts/{account.id}/recommendations/bulk-state",
        json={
            "recommendation_ids": [str(rec.id)],
            "action": "dismiss",
            "reason": "false_positive",
            "note": "Detected by test batch",
        },
        headers=dev_org_scope["headers"],
    )
    assert bulk_dismiss.status_code == 200
    assert bulk_dismiss.json()["updated_count"] == 1
