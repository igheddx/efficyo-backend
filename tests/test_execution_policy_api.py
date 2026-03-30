"""Execution policies + eligibility API."""

from app.models.cloud_account import CloudAccount
from app.models.execution_policy import ExecutionPolicy
from app.models.recommendation import Recommendation
from app.models.recommendation_outcome import RecommendationOutcome
from app.models.resource_snapshot import ResourceSnapshot
from app.models.tenant import Tenant
from app.core.db import utc_now


def _seed_rec(db, org_id):
    tenant = Tenant(name="ep-tenant", organization_id=org_id)
    db.add(tenant)
    db.flush()
    ca = CloudAccount(
        tenant_id=tenant.id,
        account_id="123456789012",
        name="ep-acct",
        role_arn="arn:aws:iam::123456789012:role/R",
        region_default="us-east-1",
    )
    db.add(ca)
    db.flush()
    snap = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=ca.id,
        resource_id="b1",
        resource_type="s3_bucket",
        region="us-east-1",
        configuration_json={},
        tags_json={},
        captured_at=utc_now(),
    )
    db.add(snap)
    db.flush()
    from app.models.finding import Finding

    f = Finding(
        tenant_id=tenant.id,
        cloud_account_id=ca.id,
        resource_snapshot_id=snap.id,
        resource_id="b1",
        resource_type="s3_bucket",
        finding_type="s3_public_access_candidate",
        severity="high",
        evidence_json={},
        detected_at=utc_now(),
    )
    db.add(f)
    db.flush()
    rec = Recommendation(
        tenant_id=tenant.id,
        cloud_account_id=ca.id,
        finding_id=f.id,
        resource_id="b1",
        resource_type="s3_bucket",
        recommendation_type="s3_enable_public_access_block",
        recommendation_category="security",
        summary="Block public access",
        explanation="x",
        risk_level="high",
        recommended_action="x",
        confidence_score="high",
    )
    db.add(rec)
    db.commit()
    return tenant, ca, rec


def test_execution_eligibility_blocked_until_approved(client, db, dev_org_scope):
    org = dev_org_scope["org"]
    tenant, ca, rec = _seed_rec(db, org.id)
    res = client.get(
        f"/api/v1/recommendations/{rec.id}/execution-eligibility"
        f"?tenant_id={tenant.id}&cloud_account_id={ca.id}",
        headers=dev_org_scope["headers"],
    )
    assert res.status_code == 200
    data = res.json()
    assert data["execution_eligible"] is False
    assert data["blocking_reason"] == "approval_required"


def test_execution_eligibility_respects_cloud_policy(client, db, dev_org_scope):
    org = dev_org_scope["org"]
    tenant, ca, rec = _seed_rec(db, org.id)
    out = RecommendationOutcome(
        tenant_id=tenant.id,
        cloud_account_id=ca.id,
        recommendation_id=rec.id,
        resource_id=rec.resource_id,
        recommendation_type=rec.recommendation_type,
        recommendation_category=rec.recommendation_category,
        status="pending",
        workflow_status="approved",
        preflight_passed_at=utc_now(),
    )
    db.add(out)
    p = ExecutionPolicy(
        organization_id=org.id,
        tenant_id=tenant.id,
        cloud_account_id=ca.id,
        recommendation_type="s3_enable_public_access_block",
        risk_class="any",
        execution_mode="approved_then_auto_allowed",
        requires_all_approvals=True,
        preflight_required=True,
        rollback_required=True,
        is_enabled=True,
    )
    db.add(p)
    db.commit()

    res = client.get(
        f"/api/v1/recommendations/{rec.id}/execution-eligibility"
        f"?tenant_id={tenant.id}&cloud_account_id={ca.id}",
        headers=dev_org_scope["headers"],
    )
    assert res.status_code == 200
    data = res.json()
    assert data["execution_eligible"] is True
    assert data["auto_execution_eligible"] is True
    assert data["policy_scope_level"] == "cloud_account"


def test_list_policies_requires_admin(client, db, dev_org_scope):
    org = dev_org_scope["org"]
    _seed_rec(db, org.id)
    h = {**dev_org_scope["headers"], "X-User": "viewer", "X-Role": "viewer"}
    res = client.get(
        f"/api/v1/execution-policies?organization_id={org.id}",
        headers=h,
    )
    assert res.status_code == 403


def test_org_admin_can_create_policy(client, db, dev_org_scope_admin):
    org = dev_org_scope_admin["org"]
    tenant, ca, _rec = _seed_rec(db, org.id)
    res = client.post(
        "/api/v1/execution-policies",
        headers=dev_org_scope_admin["headers"],
        json={
            "organization_id": str(org.id),
            "tenant_id": str(tenant.id),
            "cloud_account_id": str(ca.id),
            "recommendation_type": "s3_add_required_tags",
            "risk_class": "any",
            "execution_mode": "manual_only",
            "requires_all_approvals": True,
            "preflight_required": True,
            "rollback_required": True,
            "is_enabled": True,
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["execution_mode"] == "manual_only"
    assert body["preflight_required"] is True


def test_auto_mode_rejects_non_allowlisted_type(client, db, dev_org_scope_admin):
    org = dev_org_scope_admin["org"]
    tenant, ca, _rec = _seed_rec(db, org.id)
    res = client.post(
        "/api/v1/execution-policies",
        headers=dev_org_scope_admin["headers"],
        json={
            "organization_id": str(org.id),
            "recommendation_type": "nat_gateway_cost_review",
            "risk_class": "any",
            "execution_mode": "approved_then_auto_allowed",
            "requires_all_approvals": True,
            "preflight_required": False,
            "rollback_required": True,
            "is_enabled": True,
        },
    )
    assert res.status_code == 400
