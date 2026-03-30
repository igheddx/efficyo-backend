"""Session-backed endpoints for editing saved MSP default org / tenant / cloud context."""

from uuid import uuid4

from app.models.access_grant import AccessGrant
from app.models.cloud_account import CloudAccount
from app.models.organization import Organization, OrgMembership
from app.models.tenant import Tenant
from app.services import auth_service


def test_me_context_defaults_tenants_and_cloud_accounts(client, db):
    slug = uuid4().hex[:10]
    org = Organization(name=f"CtxDef Org {slug}", slug=f"ctx-{slug}", status="active")
    db.add(org)
    db.commit()
    db.refresh(org)

    user = auth_service.create_user(
        db,
        email=f"u-{slug}@test.local",
        password="pwtest12",
        display_name="U",
    )
    db.add(
        OrgMembership(
            organization_id=org.id,
            user_id=user.id,
            user_identifier=user.email,
            role="member",
        )
    )
    db.commit()

    tenant = Tenant(name=f"tenant-{slug}", status="active", organization_id=org.id)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    ca = CloudAccount(
        tenant_id=tenant.id,
        account_id="123456789012",
        name="acct1",
        status="pending",
        connection_status="untested",
        role_arn="arn:aws:iam::123456789012:role/x",
        region_default="us-east-1",
    )
    db.add(ca)
    db.commit()
    db.refresh(ca)

    db.add(
        AccessGrant(
            organization_id=org.id,
            user_id=user.id,
            tenant_id=tenant.id,
            cloud_account_id=None,
            access_role="viewer",
        )
    )
    db.commit()

    login = client.post("/api/v1/login", json={"login": user.email, "password": "pwtest12"})
    assert login.status_code == 200

    r_tenants = client.get(
        "/api/v1/me/context-defaults/tenants",
        params={"organization_id": str(org.id)},
    )
    assert r_tenants.status_code == 200
    tenants_payload = r_tenants.json()
    assert len(tenants_payload) == 1
    assert tenants_payload[0]["id"] == str(tenant.id)

    r_cloud = client.get(
        "/api/v1/me/context-defaults/cloud-accounts",
        params={"tenant_id": str(tenant.id)},
    )
    assert r_cloud.status_code == 200
    cloud_payload = r_cloud.json()
    assert len(cloud_payload) == 1
    assert cloud_payload[0]["id"] == str(ca.id)
