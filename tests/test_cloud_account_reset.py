"""Reset ingestion-derived data for a cloud account."""

from uuid import uuid4

from fastapi import status

from app.models.cloud_account import CloudAccount
from app.models.finding import Finding
from app.models.ingestion_job import IngestionJob
from app.models.policy_profile import PolicyProfile
from app.models.recommendation import Recommendation
from app.models.recommendation_outcome import RecommendationOutcome
from app.models.resource_snapshot import ResourceSnapshot
from app.models.tenant import Tenant


def _seed_scope(db, tenant_id, cloud_id, organization_id):
    tenant = Tenant(id=tenant_id, name="T", status="active", organization_id=organization_id)
    db.add(tenant)
    db.add(PolicyProfile(tenant_id=tenant_id, name="default", config_json={}))
    cloud = CloudAccount(
        id=cloud_id,
        tenant_id=tenant_id,
        account_id="123456789012",
        name="AWS",
        status="pending",
        role_arn="arn:aws:iam::123456789012:role/x",
        region_default="us-east-1",
    )
    db.add(cloud)
    db.commit()

    snap = ResourceSnapshot(
        tenant_id=tenant_id,
        cloud_account_id=cloud_id,
        resource_id="i-1",
        resource_type="ec2_instance",
        region="us-east-1",
        configuration_json={},
        tags_json={},
    )
    db.add(snap)
    db.flush()
    fin = Finding(
        tenant_id=tenant_id,
        cloud_account_id=cloud_id,
        resource_snapshot_id=snap.id,
        resource_id="i-1",
        resource_type="ec2_instance",
        finding_type="ec2_missing_required_tags",
        severity="medium",
        evidence_json={},
    )
    db.add(fin)
    db.flush()
    rec = Recommendation(
        tenant_id=tenant_id,
        cloud_account_id=cloud_id,
        finding_id=fin.id,
        resource_id="i-1",
        resource_type="ec2_instance",
        recommendation_type="t",
        recommendation_category="cost",
        summary="s",
        explanation="e",
        risk_level="low",
        recommended_action="a",
    )
    db.add(rec)
    db.flush()
    db.add(
        RecommendationOutcome(
            tenant_id=tenant_id,
            cloud_account_id=cloud_id,
            recommendation_id=rec.id,
            resource_id="i-1",
            recommendation_type="t",
            recommendation_category="cost",
            status="pending",
        )
    )
    db.add(IngestionJob(tenant_id=tenant_id, cloud_account_id=cloud_id, job_type="full_sync", status="completed"))
    db.commit()


def test_reset_endpoint_forbidden_without_root_admin(client, db, dev_org_scope):
    tid, cid = uuid4(), uuid4()
    org_id = dev_org_scope["org"].id
    _seed_scope(db, tid, cid, org_id)
    h = {
        **dev_org_scope["headers"],
        "X-User": "u",
        "X-Role": "viewer",
        "X-Current-Organization-Id": str(org_id),
    }

    res = client.post(
        f"/api/v1/tenants/{tid}/cloud-accounts/{cid}/ingestion-data/reset",
        headers=h,
    )
    assert res.status_code == status.HTTP_403_FORBIDDEN


def test_reset_endpoint_clears_rows(client, db, dev_org_scope):
    tid, cid = uuid4(), uuid4()
    org_id = dev_org_scope["org"].id
    _seed_scope(db, tid, cid, org_id)
    h = {
        **dev_org_scope["headers"],
        "X-User": "u",
        "X-Role": "root_admin",
        "X-Current-Organization-Id": str(org_id),
    }

    res = client.post(
        f"/api/v1/tenants/{tid}/cloud-accounts/{cid}/ingestion-data/reset",
        headers=h,
    )
    assert res.status_code == status.HTTP_200_OK
    body = res.json()
    assert body["deleted"]["resource_snapshots"] >= 1
    assert body["deleted"]["findings"] >= 1
    assert body["deleted"]["recommendations"] >= 1
    assert body["deleted"]["recommendation_outcomes"] >= 1
    assert body["deleted"]["ingestion_jobs"] >= 1

    assert db.query(ResourceSnapshot).filter(ResourceSnapshot.tenant_id == tid).count() == 0
    assert db.query(Finding).filter(Finding.tenant_id == tid).count() == 0
