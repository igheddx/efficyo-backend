"""Tests for EC2 and EBS detection flows."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.models.cloud_account import CloudAccount
from app.models.finding import Finding
from app.models.resource_snapshot import ResourceSnapshot
from app.models.tenant import Tenant
from app.models.recommendation import Recommendation
from app.services import detection_service, recommendation_service


def _create_tenant_and_account(db):
    tenant = Tenant(name="detection-tenant", status="active")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    cloud_account = CloudAccount(
        tenant_id=tenant.id,
        account_id="123456789012",
        name="detection-account",
        status="connected",
        role_arn="arn:aws:iam::123456789012:role/fptnext-validator",
        region_default="us-east-1",
    )
    db.add(cloud_account)
    db.commit()
    db.refresh(cloud_account)

    return tenant, cloud_account


def test_detect_ec2_findings_creates_stopped_and_missing_tag_findings(db):
    tenant, cloud_account = _create_tenant_and_account(db)

    snapshot = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=cloud_account.id,
        resource_id="i-0123456789abcdef0",
        resource_type="ec2_instance",
        region="us-east-1",
        configuration_json={
            "instance_type": "t3.micro",
            "state": "stopped",
            "availability_zone": "us-east-1a",
        },
        tags_json={},
    )
    db.add(snapshot)
    db.commit()

    run_id = uuid4()
    result = detection_service.detect_ec2_findings(db, tenant.id, cloud_account.id, run_id)

    assert result.resource_type == "ec2_instance"
    assert result.sync_run_id == run_id
    assert result.findings_created == 2

    finding_types = {
        finding.finding_type
        for finding in db.query(Finding)
        .filter(Finding.tenant_id == tenant.id, Finding.cloud_account_id == cloud_account.id)
        .all()
    }
    assert "ec2_stopped_instance" in finding_types
    assert "ec2_missing_required_tags" in finding_types


def test_detect_rds_findings_dedupes_aurora_serverless_instance_and_cluster(db):
    """Writer (db.serverless) and cluster snapshots must not emit two serverless review findings."""
    tenant, cloud_account = _create_tenant_and_account(db)

    inst = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=cloud_account.id,
        resource_id="tipply-db-cluster1-instance-1",
        resource_type="rds_instance",
        region="us-east-1",
        configuration_json={
            "db_instance_class": "db.serverless",
            "engine": "aurora-postgresql",
            "db_cluster_identifier": "tipply-db-cluster1",
            "publicly_accessible": False,
        },
        tags_json={"Name": "writer", "Environment": "prod"},
    )
    cluster = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=cloud_account.id,
        resource_id="tipply-db-cluster1",
        resource_type="aurora_cluster",
        region="us-east-1",
        configuration_json={
            "engine": "aurora-postgresql",
            "engine_mode": "provisioned",
            "serverless_v2_scaling": {"MinCapacity": 0.5, "MaxCapacity": 16},
            "publicly_accessible": False,
        },
        tags_json={"Name": "cluster", "Environment": "prod"},
    )
    db.add_all([inst, cluster])
    db.commit()

    run_id = uuid4()
    detection_service.detect_rds_findings(db, tenant.id, cloud_account.id, run_id)

    serverless = [
        f
        for f in db.query(Finding)
        .filter(Finding.tenant_id == tenant.id, Finding.cloud_account_id == cloud_account.id)
        .all()
        if f.finding_type == "aurora_serverless_review_candidate"
    ]
    assert len(serverless) == 1
    assert serverless[0].resource_type == "aurora_cluster"
    assert serverless[0].resource_id == "tipply-db-cluster1"


def test_list_findings_dedupes_aurora_serverless_same_cluster(db):
    """API listing must not return writer + cluster rows as two separate findings."""
    tenant, cloud_account = _create_tenant_and_account(db)
    now = datetime.now(timezone.utc)

    s_inst = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=cloud_account.id,
        resource_id="legacy-app-instance-1",
        resource_type="rds_instance",
        region="us-east-1",
        configuration_json={},
        tags_json={},
        captured_at=now,
    )
    s_cluster = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=cloud_account.id,
        resource_id="legacy-app",
        resource_type="aurora_cluster",
        region="us-east-1",
        configuration_json={},
        tags_json={},
        captured_at=now,
    )
    db.add_all([s_inst, s_cluster])
    db.commit()
    db.refresh(s_inst)
    db.refresh(s_cluster)

    f_inst = Finding(
        tenant_id=tenant.id,
        cloud_account_id=cloud_account.id,
        resource_snapshot_id=s_inst.id,
        resource_id="legacy-app-instance-1",
        resource_type="rds_instance",
        finding_type="aurora_serverless_review_candidate",
        severity="medium",
        evidence_json={},
        detected_at=now - timedelta(hours=1),
    )
    f_cluster = Finding(
        tenant_id=tenant.id,
        cloud_account_id=cloud_account.id,
        resource_snapshot_id=s_cluster.id,
        resource_id="legacy-app",
        resource_type="aurora_cluster",
        finding_type="aurora_serverless_review_candidate",
        severity="medium",
        evidence_json={},
        detected_at=now,
    )
    db.add_all([f_inst, f_cluster])
    db.commit()

    listed = detection_service.list_findings(db, tenant.id, cloud_account.id)
    aurora = [f for f in listed if f.finding_type == "aurora_serverless_review_candidate"]
    assert len(aurora) == 1
    assert aurora[0].resource_type == "aurora_cluster"
    assert aurora[0].resource_id == "legacy-app"


def test_detect_rds_findings_aurora_serverless_v2_cluster_snapshot(db):
    """Aurora Serverless v2 clusters are ingested as aurora_cluster, not db.serverless instances."""
    tenant, cloud_account = _create_tenant_and_account(db)

    snapshot = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=cloud_account.id,
        resource_id="tipwave-aurora",
        resource_type="aurora_cluster",
        region="us-east-1",
        configuration_json={
            "engine": "aurora-postgresql",
            "engine_mode": "provisioned",
            "serverless_v2_scaling": {"MinCapacity": 0.5, "MaxCapacity": 16},
        },
        tags_json={"Name": "app-db", "Environment": "prod"},
    )
    db.add(snapshot)
    db.commit()

    run_id = uuid4()
    result = detection_service.detect_rds_findings(db, tenant.id, cloud_account.id, run_id)

    assert result.findings_created >= 1
    assert result.sync_run_id == run_id
    types = {
        f.finding_type
        for f in db.query(Finding)
        .filter(Finding.tenant_id == tenant.id, Finding.cloud_account_id == cloud_account.id)
        .all()
    }
    assert "aurora_serverless_review_candidate" in types


def test_detect_rds_findings_aurora_cluster_publicly_accessible(db):
    tenant, cloud_account = _create_tenant_and_account(db)

    snapshot = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=cloud_account.id,
        resource_id="aurora-main",
        resource_type="aurora_cluster",
        region="us-east-1",
        configuration_json={
            "engine": "aurora-postgresql",
            "engine_mode": "provisioned",
            "publicly_accessible": True,
        },
        tags_json={"Name": "app-db", "Environment": "prod"},
    )
    db.add(snapshot)
    db.commit()

    run_id = uuid4()
    result = detection_service.detect_rds_findings(db, tenant.id, cloud_account.id, run_id)

    assert result.findings_created >= 1
    assert result.sync_run_id == run_id
    types = {
        f.finding_type
        for f in db.query(Finding)
        .filter(Finding.tenant_id == tenant.id, Finding.cloud_account_id == cloud_account.id)
        .all()
    }
    assert "rds_publicly_accessible" in types


def test_detect_rds_findings_uses_latest_snapshot_per_resource_type(db):
    """If instance and cluster rows have different captured_at, use the newest batch for each type."""
    tenant, cloud_account = _create_tenant_and_account(db)
    older = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    newer = older + timedelta(days=1)

    db.add(
        ResourceSnapshot(
            tenant_id=tenant.id,
            cloud_account_id=cloud_account.id,
            resource_id="aurora-writer",
            resource_type="rds_instance",
            region="us-east-1",
            configuration_json={
                "engine": "aurora-postgresql",
                "publicly_accessible": True,
                "db_cluster_identifier": "aurora-main",
            },
            tags_json={"Name": "w", "Environment": "prod"},
            captured_at=older,
        )
    )
    db.add(
        ResourceSnapshot(
            tenant_id=tenant.id,
            cloud_account_id=cloud_account.id,
            resource_id="aurora-main",
            resource_type="aurora_cluster",
            region="us-east-1",
            configuration_json={
                "engine": "aurora-postgresql",
                "publicly_accessible": True,
            },
            tags_json={"Name": "c", "Environment": "prod"},
            captured_at=newer,
        )
    )
    db.commit()

    detection_service.detect_rds_findings(db, tenant.id, cloud_account.id, uuid4())

    types = {
        f.finding_type
        for f in db.query(Finding)
        .filter(Finding.tenant_id == tenant.id, Finding.cloud_account_id == cloud_account.id)
        .all()
    }
    assert "rds_publicly_accessible" in types


def test_detect_ebs_findings_creates_unattached_and_missing_tag_findings(db):
    tenant, cloud_account = _create_tenant_and_account(db)

    snapshot = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=cloud_account.id,
        resource_id="vol-0123456789abcdef0",
        resource_type="ebs_volume",
        region="us-east-1",
        configuration_json={
            "size_gb": 100,
            "state": "available",
            "attachments": [],
        },
        tags_json={"Name": "data-volume"},
    )
    db.add(snapshot)
    db.commit()

    run_id = uuid4()
    result = detection_service.detect_ebs_findings(db, tenant.id, cloud_account.id, run_id)

    assert result.resource_type == "ebs_volume"
    assert result.sync_run_id == run_id
    assert result.findings_created == 2

    finding_types = {
        finding.finding_type
        for finding in db.query(Finding)
        .filter(Finding.tenant_id == tenant.id, Finding.cloud_account_id == cloud_account.id)
        .all()
    }
    assert "ebs_unattached_volume" in finding_types
    assert "ebs_missing_required_tags" in finding_types


def test_generate_rds_recommendations_includes_all_findings_for_same_sync_run(db):
    """Same sync_run_id must include findings even when detected_at differs per detector."""
    tenant, cloud_account = _create_tenant_and_account(db)

    snapshot = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=cloud_account.id,
        resource_id="fn-a",
        resource_type="lambda_function",
        region="us-east-1",
        configuration_json={},
        tags_json={},
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)

    run_id = uuid4()
    early = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
    late = early + timedelta(minutes=1)

    db.add_all(
        [
            Finding(
                tenant_id=tenant.id,
                cloud_account_id=cloud_account.id,
                resource_snapshot_id=snapshot.id,
                resource_id="fn-a",
                resource_type="lambda_function",
                finding_type="lambda_missing_required_tags",
                severity="medium",
                evidence_json={"missing_tags": ["Name"], "tags": {}},
                detected_at=early,
                sync_run_id=run_id,
            ),
            Finding(
                tenant_id=tenant.id,
                cloud_account_id=cloud_account.id,
                resource_snapshot_id=snapshot.id,
                resource_id="bucket-b",
                resource_type="s3_bucket",
                finding_type="s3_missing_required_tags",
                severity="medium",
                evidence_json={"missing_tags": ["Name"], "tags": {}},
                detected_at=late,
                sync_run_id=run_id,
            ),
        ]
    )
    db.commit()

    result = recommendation_service.generate_rds_recommendations(
        db, tenant.id, cloud_account.id, sync_run_id=run_id
    )
    assert result.recommendations_created == 2
    assert (
        db.query(Recommendation)
        .filter(Recommendation.cloud_account_id == cloud_account.id)
        .count()
        == 2
    )


def test_detect_ec2_endpoint_success(client, db, dev_org_scope):
    tenant, cloud_account = _create_tenant_and_account(db)
    tenant.organization_id = dev_org_scope["org"].id
    db.add(tenant)
    db.commit()

    snapshot = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=cloud_account.id,
        resource_id="i-0123456789abcdef0",
        resource_type="ec2_instance",
        region="us-east-1",
        configuration_json={"instance_type": "t3.micro", "state": "stopped", "availability_zone": "us-east-1a"},
        tags_json={"Name": "web"},
    )
    db.add(snapshot)
    db.commit()

    response = client.post(
        f"/api/v1/tenants/{tenant.id}/cloud-accounts/{cloud_account.id}/detect/ec2",
        headers=dev_org_scope["headers"],
    )

    assert response.status_code == 200
    data = response.json()
    assert data["resource_type"] == "ec2_instance"
    assert data["findings_created"] == 2
    assert data.get("sync_run_id")


def test_detect_ebs_endpoint_success(client, db, dev_org_scope):
    tenant, cloud_account = _create_tenant_and_account(db)
    tenant.organization_id = dev_org_scope["org"].id
    db.add(tenant)
    db.commit()

    snapshot = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=cloud_account.id,
        resource_id="vol-0123456789abcdef0",
        resource_type="ebs_volume",
        region="us-east-1",
        configuration_json={"size_gb": 40, "state": "available", "attachments": []},
        tags_json={"Name": "data"},
    )
    db.add(snapshot)
    db.commit()

    response = client.post(
        f"/api/v1/tenants/{tenant.id}/cloud-accounts/{cloud_account.id}/detect/ebs",
        headers=dev_org_scope["headers"],
    )

    assert response.status_code == 200
    data = response.json()
    assert data["resource_type"] == "ebs_volume"
    assert data["findings_created"] == 2
    assert data.get("sync_run_id")
