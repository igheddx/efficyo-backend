"""Pending approvals and reject workflow API."""

from uuid import uuid4

from app.core.db import utc_now
from app.models.cloud_account import CloudAccount
from app.models.finding import Finding
from app.models.recommendation import Recommendation
from app.models.resource_snapshot import ResourceSnapshot
from app.models.tenant import Tenant


def _seed_rec_for_approval(db, org):
    t = Tenant(name=f"cust-{uuid4().hex[:10]}", organization_id=org.id)
    db.add(t)
    db.commit()
    db.refresh(t)
    ca = CloudAccount(
        tenant_id=t.id,
        account_id="123456789012",
        name="acct",
        role_arn="arn:aws:iam::123456789012:role/R",
        region_default="us-east-1",
    )
    db.add(ca)
    db.commit()
    db.refresh(ca)
    snap = ResourceSnapshot(
        tenant_id=t.id,
        cloud_account_id=ca.id,
        resource_id="r1",
        resource_type="ec2",
        region="us-east-1",
        configuration_json={},
        tags_json={},
        captured_at=utc_now(),
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)
    f = Finding(
        tenant_id=t.id,
        cloud_account_id=ca.id,
        resource_snapshot_id=snap.id,
        resource_id="r1",
        resource_type="ec2",
        finding_type="cost",
        severity="medium",
        evidence_json={},
    )
    db.add(f)
    db.commit()
    db.refresh(f)
    rec = Recommendation(
        tenant_id=t.id,
        cloud_account_id=ca.id,
        finding_id=f.id,
        resource_id="i-1",
        resource_type="ec2",
        recommendation_type="test_rec",
        recommendation_category="cost",
        summary="Resize instance",
        explanation="Too large",
        risk_level="medium",
        recommended_action="downsize",
        confidence_score="medium",
        estimated_savings=42.5,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return t, ca, rec


def test_pending_approvals_lists_suggestion(client, db, dev_org_scope):
    org = dev_org_scope["org"]
    h = dev_org_scope["headers"]
    _seed_rec_for_approval(db, org)
    r = client.get("/api/v1/approvals/pending", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 1
    assert any(item["summary"] == "Resize instance" for item in data["items"])


def test_pending_count(client, db, dev_org_scope):
    org = dev_org_scope["org"]
    h = dev_org_scope["headers"]
    _seed_rec_for_approval(db, org)
    r = client.get("/api/v1/approvals/pending/count", headers=h)
    assert r.status_code == 200
    assert r.json()["count"] >= 1


def test_pending_viewer_forbidden(client, dev_org_scope):
    h = {**dev_org_scope["headers"], "X-Role": "viewer", "X-User": "v"}
    r = client.get("/api/v1/approvals/pending", headers=h)
    assert r.status_code == 403


def test_reject_recommendation(client, db, dev_org_scope):
    org = dev_org_scope["org"]
    h = dev_org_scope["headers"]
    _t, ca, rec = _seed_rec_for_approval(db, org)
    url = f"/api/v1/tenants/{_t.id}/cloud-accounts/{ca.id}/recommendations/{rec.id}/reject"
    r = client.post(url, headers=h, json={"rejection_reason": "Not in scope this quarter."})
    assert r.status_code == 200
    body = r.json()
    assert body["workflow_status"] == "rejected"
    assert "Not in scope" in (body.get("rejection_reason") or "")

    r2 = client.get("/api/v1/approvals/pending", headers=h)
    assert r2.status_code == 200
    ids = {item["recommendation_id"] for item in r2.json()["items"]}
    assert str(rec.id) not in ids


def test_approve_with_comment(client, db, dev_org_scope):
    org = dev_org_scope["org"]
    h = dev_org_scope["headers"]
    _t, ca, rec = _seed_rec_for_approval(db, org)
    url = f"/api/v1/tenants/{_t.id}/cloud-accounts/{ca.id}/recommendations/{rec.id}/approve"
    r = client.post(url, headers=h, json={"approval_comment": "LGTM after cost review."})
    assert r.status_code == 200
    assert r.json()["workflow_status"] == "approved"
    assert "LGTM" in (r.json().get("approval_comment") or "")
