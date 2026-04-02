# Tests for tenant API endpoints

from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from app.models.policy_profile import PolicyProfile
from app.models.tenant import Tenant
from app.services import create_tenant, get_tenant, list_tenants
from app.services.tenant_service import TIPWAVE_TENANT_ID, ensure_demo_tenants


class TestTenantService:
    """Test tenant service layer."""

    def test_create_tenant_with_default_policy(self, db: Session):
        """Creating a tenant should create a default policy profile."""
        tenant = create_tenant(db, "test-tenant")

        assert tenant.name == "test-tenant"
        assert tenant.status == "active"
        assert len(tenant.policy_profiles) == 1
        assert tenant.policy_profiles[0].name == "default"
        assert tenant.policy_profiles[0].config_json == {}

    def test_get_tenant(self, db: Session):
        """Should retrieve a tenant by ID."""
        created = create_tenant(db, "test-tenant")
        retrieved = get_tenant(db, created.id)

        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.name == "test-tenant"

    def test_list_tenants(self, db: Session):
        """Should list all tenants."""
        create_tenant(db, "tenant-1")
        create_tenant(db, "tenant-2")

        tenants = list_tenants(db)

        assert len(tenants) == 2
        assert tenants[0].name == "tenant-1"
        assert tenants[1].name == "tenant-2"

    def test_ensure_demo_tenants_frees_tipwave_name_when_stable_row_not_named_tipwave(self, db: Session):
        """If another tenant holds 'Tipwave', rename it before assigning the canonical Tipwave UUID row."""
        dup_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        db.add(Tenant(id=dup_id, name="Tipwave", status="active"))
        db.add(Tenant(id=TIPWAVE_TENANT_ID, name="Canonical Row Wrong Label", status="active"))
        db.add(PolicyProfile(tenant_id=dup_id, name="default", config_json={}))
        db.add(PolicyProfile(tenant_id=TIPWAVE_TENANT_ID, name="default", config_json={}))
        db.commit()

        ensure_demo_tenants(db)

        stable = db.query(Tenant).filter(Tenant.id == TIPWAVE_TENANT_ID).one()
        assert stable.name == "Tipwave"
        other = db.query(Tenant).filter(Tenant.id == dup_id).one()
        assert other.name != "Tipwave"


class TestTenantAPI:
    """Test tenant API endpoints."""

    def test_post_tenants_creates_tenant_as_msp_admin(self, client, dev_org_scope_admin):
        """Membership `admin` can create customer tenants for their org."""
        h = dev_org_scope_admin["headers"]
        response = client.post("/api/v1/tenants", json={"name": "customer-a"}, headers=h)
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "customer-a"

    def test_post_tenants_creates_tenant(self, client, dev_org_scope):
        """POST /api/v1/tenants should create a tenant."""
        h = dev_org_scope["headers"]
        response = client.post(
            "/api/v1/tenants",
            json={"name": "test-tenant"},
            headers=h,
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "test-tenant"
        assert data["status"] == "active"
        assert isinstance(data["id"], str)  # UUID as string

    def test_post_tenants_with_organization_id_platform_root(self, client, dev_org_scope):
        """Platform root can create a customer under a specific org without session org."""
        org = dev_org_scope["org"]
        h = {"X-User": "root", "X-Role": "root_admin"}
        response = client.post(
            "/api/v1/tenants",
            json={"name": "customer-by-root", "organization_id": str(org.id)},
            headers=h,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "customer-by-root"
        assert data["organization_id"] == str(org.id)

    def test_post_tenants_rejects_organization_id_for_non_root(self, client, dev_org_scope):
        """Non–platform-root callers cannot set organization_id on create."""
        h = dev_org_scope["headers"]
        response = client.post(
            "/api/v1/tenants",
            json={"name": "should-fail", "organization_id": str(dev_org_scope["org"].id)},
            headers=h,
        )
        assert response.status_code == 403
        assert "platform" in response.json()["detail"].lower()

    def test_post_tenants_duplicate_name_conflict(self, client, dev_org_scope):
        """POST /api/v1/tenants with duplicate name should return 409."""
        h = dev_org_scope["headers"]
        client.post("/api/v1/tenants", json={"name": "test-tenant"}, headers=h)
        response = client.post("/api/v1/tenants", json={"name": "test-tenant"}, headers=h)

        assert response.status_code == 409
        assert "already exists" in response.json()["detail"]

    def test_get_tenants_lists_all(self, client, dev_org_scope):
        """GET /api/v1/tenants should return tenants for the current org only."""
        h = dev_org_scope["headers"]
        client.post("/api/v1/tenants", json={"name": "tenant-1"}, headers=h)
        client.post("/api/v1/tenants", json={"name": "tenant-2"}, headers=h)

        response = client.get("/api/v1/tenants", headers=h)

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        names = {row["name"] for row in data}
        assert names == {"tenant-1", "tenant-2"}
        for row in data:
            assert row["organization_id"] == str(dev_org_scope["org"].id)

    def test_get_tenant_by_id_returns_tenant(self, client, dev_org_scope):
        """GET /api/v1/tenants/{tenant_id} should return one tenant."""
        h = dev_org_scope["headers"]
        create_response = client.post("/api/v1/tenants", json={"name": "test-tenant"}, headers=h)
        tenant_id = create_response.json()["id"]

        response = client.get(f"/api/v1/tenants/{tenant_id}", headers=h)

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == tenant_id
        assert data["name"] == "test-tenant"

    def test_get_tenant_by_id_not_found(self, client, dev_org_scope):
        """GET /api/v1/tenants/{tenant_id} with invalid ID should return 404."""
        fake_id = "00000000-0000-0000-0000-000000000000"
        response = client.get(f"/api/v1/tenants/{fake_id}", headers=dev_org_scope["headers"])

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]
