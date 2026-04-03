from datetime import datetime, timezone
from uuid import uuid4

from app.models.cloud_account import CloudAccount
from app.models.finding import Finding
from app.models.organization import Organization
from app.models.resource_snapshot import ResourceSnapshot
from app.models.tenant import Tenant
from app.services import detection_extended_service


def _seed_scope(db):
    org = Organization(name="tg-org", slug=f"tg-org-{uuid4().hex[:8]}")
    db.add(org)
    db.commit()
    db.refresh(org)

    tenant = Tenant(name="tg-tenant", status="active", organization_id=org.id)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    account = CloudAccount(
        tenant_id=tenant.id,
        account_id="123456789012",
        name="tg-account",
        status="connected",
        role_arn="arn:aws:iam::123456789012:role/fptnext-validator",
        region_default="us-east-1",
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return tenant, account


def test_detect_extended_target_group_depth_findings(db):
    tenant, account = _seed_scope(db)
    captured_at = datetime(2026, 4, 2, tzinfo=timezone.utc)

    tg = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=account.id,
        resource_id="arn:aws:elasticloadbalancing:us-east-1:123:targetgroup/tg-a/abc",
        resource_type="target_group",
        region="us-east-1",
        configuration_json={
            "target_group_name": "tg-a",
            "load_balancer_arn": "arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/lb-a/xyz",
            "total_targets": 0,
            "healthy_count": 0,
            "unhealthy_count": 2,
            "health_check_enabled": False,
            "health_check_interval_seconds": 0,
            "health_check_timeout_seconds": 0,
            "unhealthy_threshold_count": 0,
            "target_health_states": {"unhealthy": 2},
        },
        tags_json={"Name": "tg-a", "Environment": "test"},
        captured_at=captured_at,
    )

    db.add(tg)
    db.commit()

    run_id = uuid4()
    detection_extended_service.detect_extended_findings(db, tenant.id, account.id, run_id)

    finding_types = {
        f.finding_type
        for f in db.query(Finding)
        .filter(Finding.tenant_id == tenant.id, Finding.cloud_account_id == account.id)
        .all()
    }

    assert "target_group_no_targets" in finding_types
    assert "target_group_unhealthy_targets" in finding_types
    assert "target_group_misconfigured_health_check" in finding_types


def test_detect_extended_network_exposure_chain_findings(db):
    tenant, account = _seed_scope(db)
    captured_at = datetime(2026, 4, 2, tzinfo=timezone.utc)

    subnet = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=account.id,
        resource_id="subnet-1",
        resource_type="subnet",
        region="us-east-1",
        configuration_json={"map_public_ip_on_launch": True, "vpc_id": "vpc-1"},
        tags_json={"Name": "public-subnet", "Environment": "test"},
        captured_at=captured_at,
    )
    route_table = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=account.id,
        resource_id="rtb-1",
        resource_type="route_table",
        region="us-east-1",
        configuration_json={
            "association_count": 1,
            "has_igw_default_route": True,
            "has_nat_default_route": False,
            "linked_resources": [{"resource_type": "subnet", "resource_id": "subnet-1"}],
        },
        tags_json={"Name": "rtb-1", "Environment": "test"},
        captured_at=captured_at,
    )
    sg = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=account.id,
        resource_id="sg-1",
        resource_type="security_group",
        region="us-east-1",
        configuration_json={
            "has_world_open_ssh": True,
            "has_world_open_rdp": False,
            "has_world_open_all_ports": False,
            "ingress_rule_count": 1,
        },
        tags_json={"Name": "sg-1", "Environment": "test"},
        captured_at=captured_at,
    )

    db.add_all([subnet, route_table, sg])
    db.commit()

    run_id = uuid4()
    detection_extended_service.detect_extended_findings(db, tenant.id, account.id, run_id)

    finding_types = {
        f.finding_type
        for f in db.query(Finding)
        .filter(Finding.tenant_id == tenant.id, Finding.cloud_account_id == account.id)
        .all()
    }

    assert "public_subnet_with_open_sg" in finding_types
    assert "internet_exposed_resource_chain" in finding_types
