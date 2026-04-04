from datetime import datetime, timezone
from uuid import uuid4

from app.models.cloud_account import CloudAccount
from app.models.finding import Finding
from app.models.organization import Organization
from app.models.resource_snapshot import ResourceSnapshot
from app.models.tenant import Tenant
from app.rules.engine import run_rule_engine
from app.services import detection_extended_service


def _seed_scope(db):
    org = Organization(name="rt-org", slug=f"rt-org-{uuid4().hex[:8]}")
    db.add(org)
    db.commit()
    db.refresh(org)

    tenant = Tenant(name="rt-tenant", status="active", organization_id=org.id)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    account = CloudAccount(
        tenant_id=tenant.id,
        account_id="123456789012",
        name="rt-account",
        status="connected",
        role_arn="arn:aws:iam::123456789012:role/fptnext-validator",
        region_default="us-east-1",
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return tenant, account


def test_detect_extended_route_table_findings(db):
    tenant, account = _seed_scope(db)
    captured_at = datetime(2026, 4, 2, 0, 0, 0, tzinfo=timezone.utc)

    rt_unused = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=account.id,
        resource_id="rtb-unused",
        resource_type="route_table",
        region="us-east-1",
        configuration_json={
            "association_count": 0,
            "route_count": 1,
            "has_igw_default_route": False,
            "has_nat_default_route": False,
            "linked_resources": [],
        },
        tags_json={"Name": "unused"},
        captured_at=captured_at,
    )
    rt_public = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=account.id,
        resource_id="rtb-public",
        resource_type="route_table",
        region="us-east-1",
        configuration_json={
            "association_count": 2,
            "route_count": 3,
            "has_igw_default_route": True,
            "has_nat_default_route": False,
            "linked_resources": [{"resource_type": "subnet", "resource_id": "subnet-123"}],
        },
        tags_json={"Name": "public", "Environment": "prod"},
        captured_at=captured_at,
    )
    db.add_all([rt_unused, rt_public])
    db.commit()

    run_id = uuid4()
    # Route table finding types are now config-driven; run both paths as production does.
    detection_extended_service.detect_extended_findings(db, tenant.id, account.id, run_id)
    run_rule_engine(db, tenant.id, account.id, run_id)

    finding_types = {
        f.finding_type
        for f in db.query(Finding)
        .filter(Finding.tenant_id == tenant.id, Finding.cloud_account_id == account.id)
        .all()
    }

    assert "route_table_unassociated_review" in finding_types
    assert "route_table_public_default_route_review" in finding_types
