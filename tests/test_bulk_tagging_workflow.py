from uuid import uuid4

from app.core.db import utc_now
from app.models.cloud_account import CloudAccount
from app.models.finding import Finding
from app.models.organization import OrgMembership
from app.models.recommendation import Recommendation
from app.models.resource_snapshot import ResourceSnapshot
from app.models.tenant import Tenant
from app.services import auth_service


def _add_org_user(db, org_id, email: str, role: str):
    user = auth_service.create_user(
        db,
        email=email,
        password="testpass12",
        display_name=email.split("@")[0],
        is_root_admin=False,
    )
    db.add(
        OrgMembership(
            organization_id=org_id,
            user_id=user.id,
            user_identifier=user.email,
            role=role,
        )
    )
    db.commit()
    db.refresh(user)
    return user


def _seed_tagging_recommendations(db, org, count: int = 3):
    tenant = Tenant(name=f"cust-{uuid4().hex[:8]}", organization_id=org.id)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    cloud = CloudAccount(
        tenant_id=tenant.id,
        account_id="123456789012",
        name="acct",
        role_arn="arn:aws:iam::123456789012:role/R",
        region_default="us-east-1",
    )
    db.add(cloud)
    db.commit()
    db.refresh(cloud)

    recs = []
    for idx in range(count):
        snap = ResourceSnapshot(
            tenant_id=tenant.id,
            cloud_account_id=cloud.id,
            resource_id=f"res-{idx}",
            resource_type="ec2",
            region="us-east-1",
            configuration_json={},
            tags_json={"Name": f"res-{idx}"},
            captured_at=utc_now(),
        )
        db.add(snap)
        db.commit()
        db.refresh(snap)

        finding = Finding(
            tenant_id=tenant.id,
            cloud_account_id=cloud.id,
            resource_snapshot_id=snap.id,
            resource_id=f"res-{idx}",
            resource_type="ec2_instance",
            finding_type="ec2_missing_required_tags",
            severity="medium",
            evidence_json={"tags": {"Name": f"res-{idx}"}, "missing_tags": ["Environment"]},
        )
        db.add(finding)
        db.commit()
        db.refresh(finding)

        rec = Recommendation(
            tenant_id=tenant.id,
            cloud_account_id=cloud.id,
            finding_id=finding.id,
            resource_id=f"i-{idx}",
            resource_type="ec2_instance",
            recommendation_type="ec2_add_required_tags",
            recommendation_category="governance",
            summary="Add required tags to EC2 instance",
            explanation="Missing required tags",
            risk_level="medium",
            recommended_action="Add tags",
            confidence_score="high",
            estimated_savings=0,
        )
        db.add(rec)
        db.commit()
        db.refresh(rec)
        recs.append(rec)

    return tenant, cloud, recs


def test_grouped_findings_and_recommendations_endpoints(client, db, dev_org_scope_admin):
    org = dev_org_scope_admin["org"]
    headers = dev_org_scope_admin["headers"]
    tenant, cloud, recs = _seed_tagging_recommendations(db, org, count=2)

    rec_resp = client.get(
        f"/api/v1/tenants/{tenant.id}/cloud-accounts/{cloud.id}/recommendations/grouped",
        headers=headers,
    )
    assert rec_resp.status_code == 200, rec_resp.text
    rec_groups = rec_resp.json()
    tag_group = next((g for g in rec_groups if g["group_key"] == "ec2_add_required_tags"), None)
    assert tag_group is not None
    assert tag_group["total_count"] == len(recs)
    assert tag_group["resource_type_breakdown"]["ec2_instance"] == len(recs)
    assert tag_group["impact_summary"]["low"] == len(recs)
    assert tag_group["effort_summary"]["low"] == len(recs)
    assert tag_group["confidence_summary"]["high"] == len(recs)
    assert tag_group["actionability_summary"]["guided"] == len(recs)
    assert tag_group["priority_group_summary"]["optional_cleanup"] == len(recs)

    finding_resp = client.get(
        f"/api/v1/tenants/{tenant.id}/cloud-accounts/{cloud.id}/findings/grouped",
        headers=headers,
    )
    assert finding_resp.status_code == 200, finding_resp.text
    finding_groups = finding_resp.json()
    finding_group = next((g for g in finding_groups if g["group_key"] == "ec2_missing_required_tags"), None)
    assert finding_group is not None
    assert finding_group["total_count"] == len(recs)
    assert finding_group["related_recommendation_type"] == "ec2_add_required_tags"


def test_tagging_batch_creates_single_approval_request(client, db, dev_org_scope_admin):
    org = dev_org_scope_admin["org"]
    headers = dev_org_scope_admin["headers"]
    tenant, cloud, recs = _seed_tagging_recommendations(db, org, count=3)

    ap1 = _add_org_user(db, org.id, "batch-approver-1@test.local", "approver")
    ap2 = _add_org_user(db, org.id, "batch-approver-2@test.local", "approver")

    body = {
        "recommendation_type": "ec2_add_required_tags",
        "title": "Bulk Tagging Review",
        "required_tag_keys": ["Name", "Environment"],
        "shared_tag_values": {"Environment": "prod"},
        "resources": [
            {
                "recommendation_id": str(rec.id),
                "proposed_tags": {"Environment": "prod", "Owner": f"owner-{idx}"},
            }
            for idx, rec in enumerate(recs)
        ],
        "approver_user_ids": [str(ap1.id), str(ap2.id)],
        "execution_owner_user_id": str(ap1.id),
        "notes": "Tagging policy rollout",
    }

    r = client.post(
        f"/api/v1/tenants/{tenant.id}/cloud-accounts/{cloud.id}/tagging-batches",
        headers=headers,
        json=body,
    )
    assert r.status_code == 201, r.text
    payload = r.json()
    assert payload["approval_request_id"] is not None
    assert len(payload["resources"]) == 3
    assert payload["summary_json"]["resource_count"] == 3

    ar = client.get("/api/v1/approval-requests?limit=50", headers=headers)
    assert ar.status_code == 200, ar.text
    items = ar.json().get("items", [])
    assert len(items) == 1


def test_tagging_batch_execute_only_after_all_approvals(client, db, dev_org_scope_admin):
    org = dev_org_scope_admin["org"]
    headers = dev_org_scope_admin["headers"]
    tenant, cloud, recs = _seed_tagging_recommendations(db, org, count=2)

    ap1 = _add_org_user(db, org.id, "batch-exec-approver-1@test.local", "approver")
    ap2 = _add_org_user(db, org.id, "batch-exec-approver-2@test.local", "approver")

    create_body = {
        "recommendation_type": "ec2_add_required_tags",
        "shared_tag_values": {"Environment": "prod"},
        "resources": [
            {
                "recommendation_id": str(rec.id),
                "proposed_tags": {"Environment": "prod", "Department": "Platform"},
            }
            for rec in recs
        ],
        "approver_user_ids": [str(ap1.id), str(ap2.id)],
        "execution_owner_user_id": str(ap1.id),
    }
    created = client.post(
        f"/api/v1/tenants/{tenant.id}/cloud-accounts/{cloud.id}/tagging-batches",
        headers=headers,
        json=create_body,
    )
    assert created.status_code == 201, created.text
    batch = created.json()

    blocked = client.post(
        f"/api/v1/tenants/{tenant.id}/cloud-accounts/{cloud.id}/tagging-batches/{batch['id']}/execute",
        headers=headers,
        json={"execution_notes": "Attempt before approval"},
    )
    assert blocked.status_code == 409

    h1 = {
        "X-User": ap1.email,
        "X-Role": "approver",
        "X-Current-Organization-Id": str(org.id),
    }
    h2 = {
        "X-User": ap2.email,
        "X-Role": "approver",
        "X-Current-Organization-Id": str(org.id),
    }
    req_id = batch["approval_request_id"]
    assert client.post(f"/api/v1/approval-requests/{req_id}/approve", headers=h1, json={}).status_code == 200
    assert client.post(f"/api/v1/approval-requests/{req_id}/approve", headers=h2, json={}).status_code == 200

    executed = client.post(
        f"/api/v1/tenants/{tenant.id}/cloud-accounts/{cloud.id}/tagging-batches/{batch['id']}/execute",
        headers=headers,
        json={"execution_notes": "Bulk tags applied"},
    )
    assert executed.status_code == 200, executed.text
    stats = executed.json()
    assert stats["completed"] == 2
    assert stats["status"] in {"completed", "failed"}
