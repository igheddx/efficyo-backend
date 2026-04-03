from datetime import datetime, timezone
from uuid import uuid4

from app.models.cloud_account import CloudAccount
from app.models.finding import Finding
from app.models.organization import Organization
from app.models.resource_snapshot import ResourceSnapshot
from app.models.tenant import Tenant
from app.services import detection_service


def _seed_scope(db):
    org = Organization(name="depth-org", slug=f"depth-org-{uuid4().hex[:8]}")
    db.add(org)
    db.commit()
    db.refresh(org)

    tenant = Tenant(name="depth-tenant", status="active", organization_id=org.id)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    account = CloudAccount(
        tenant_id=tenant.id,
        account_id="123456789012",
        name="depth-account",
        status="connected",
        role_arn="arn:aws:iam::123456789012:role/fptnext-validator",
        region_default="us-east-1",
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return tenant, account


def test_depth_findings_generated_for_rds_s3_lambda(db):
    tenant, account = _seed_scope(db)
    captured_at = datetime(2026, 4, 2, tzinfo=timezone.utc)

    rds_snapshot = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=account.id,
        resource_id="db-1",
        resource_type="rds_instance",
        region="us-east-1",
        configuration_json={
            "db_instance_class": "db.m5.large",
            "allocated_storage": 500,
            "cpu_utilization_avg_7d": 2.1,
            "db_connections_avg_7d": 1.2,
            "storage_free_ratio_7d": 0.81,
            "publicly_accessible": False,
        },
        tags_json={"Name": "db-1", "Environment": "test"},
        captured_at=captured_at,
    )

    lambda_snapshot = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=account.id,
        resource_id="fn-1",
        resource_type="lambda_function",
        region="us-east-1",
        configuration_json={
            "memory_size": 2048,
            "invocations_sum_7d": 20,
            "duration_avg_ms_7d": 100,
            "concurrent_executions_max_7d": 75,
            "reserved_concurrency": None,
            "vpc_config": {"vpc_id": None, "subnet_ids": [], "security_group_ids": []},
        },
        tags_json={"Name": "fn-1", "Environment": "test"},
        captured_at=captured_at,
    )

    s3_snapshot = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=account.id,
        resource_id="bucket-1",
        resource_type="s3_bucket",
        region="us-east-1",
        configuration_json={
            "versioning_status": "Enabled",
            "encryption_enabled": False,
            "public_access_block_status": {
                "block_public_acls": True,
                "ignore_public_acls": True,
                "block_public_policy": True,
                "restrict_public_buckets": True,
            },
            "lifecycle_rules_count": 0,
            "bucket_size_bytes_approx": 150 * 1024 * 1024 * 1024,
        },
        tags_json={"Name": "bucket-1", "Environment": "test"},
        captured_at=captured_at,
    )

    db.add_all([rds_snapshot, lambda_snapshot, s3_snapshot])
    db.commit()

    run_id = uuid4()
    detection_service.detect_rds_findings(db, tenant.id, account.id, run_id)
    detection_service.detect_lambda_findings(db, tenant.id, account.id, run_id)
    detection_service.detect_s3_findings(db, tenant.id, account.id, run_id)

    types = {
        f.finding_type
        for f in db.query(Finding)
        .filter(Finding.tenant_id == tenant.id, Finding.cloud_account_id == account.id)
        .all()
    }

    assert "rds_low_cpu_underutilized" in types
    assert "rds_high_storage_unused" in types
    assert "rds_idle_instance_candidate" in types
    assert "lambda_low_utilization" in types
    assert "lambda_excessive_memory_allocation" in types
    assert "lambda_concurrency_risk" in types
    assert "s3_missing_encryption" in types
    assert "s3_no_lifecycle_large_bucket" in types
    assert "s3_infrequent_access_candidate" in types
