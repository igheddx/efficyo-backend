"""Organization and membership API permission tests."""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.models.organization import OrgMembership, Organization
from app.models.tenant import Tenant


def test_viewer_cannot_list_orgs(client: TestClient):
    r = client.get("/api/v1/orgs", headers={"X-User": "u1", "X-Role": "viewer"})
    assert r.status_code == 403


def test_root_creates_and_lists_org(client: TestClient, db):
    r = client.post(
        "/api/v1/orgs",
        json={"name": "Acme"},
        headers={"X-User": "root", "X-Role": "root_admin"},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "Acme"

    r2 = client.get("/api/v1/orgs", headers={"X-User": "root", "X-Role": "root_admin"})
    assert r2.status_code == 200
    names = {row["name"] for row in r2.json()}
    assert "Acme" in names
    assert "demo_MSP" in names  # seeded demo org for local testing


def test_org_admin_requires_db_membership(client: TestClient):
    r = client.get(
        "/api/v1/orgs",
        headers={"X-User": "alice", "X-Role": "org_admin"},
    )
    assert r.status_code == 200
    assert r.json() == []


def test_org_admin_sees_assigned_org(client: TestClient, db):
    org = Organization(name="Beta", slug="beta")
    db.add(org)
    db.commit()
    db.refresh(org)
    db.add(
        OrgMembership(
            organization_id=org.id,
            user_identifier="bob",
            role="org_admin",
        )
    )
    db.commit()

    r = client.get(
        "/api/v1/orgs",
        headers={"X-User": "bob", "X-Role": "org_admin"},
    )
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["name"] == "Beta"


def test_org_admin_cannot_create_org(client: TestClient):
    r = client.post(
        "/api/v1/orgs",
        json={"name": "Nope"},
        headers={"X-User": "bob", "X-Role": "org_admin"},
    )
    assert r.status_code == 403


def test_root_assigns_org_admin_and_org_admin_adds_user(client: TestClient, db):
    from app.services import auth_service

    auth_service.create_user(db, email="carol@gamma.test", password="testpass12", display_name="Carol")
    auth_service.create_user(db, email="dave@gamma.test", password="testpass12", display_name="Dave")

    r = client.post(
        "/api/v1/orgs",
        json={"name": "Gamma"},
        headers={"X-User": "root", "X-Role": "root_admin"},
    )
    assert r.status_code == 201
    org_id = r.json()["id"]

    r2 = client.post(
        f"/api/v1/orgs/{org_id}/users",
        json={"email": "carol@gamma.test", "role": "org_admin"},
        headers={"X-User": "root", "X-Role": "root_admin"},
    )
    assert r2.status_code == 201
    assert r2.json()["role"] == "org_admin"

    r3 = client.post(
        f"/api/v1/orgs/{org_id}/users",
        json={"email": "dave@gamma.test", "role": "viewer"},
        headers={"X-User": "carol@gamma.test", "X-Role": "org_admin"},
    )
    assert r3.status_code == 201

    r4 = client.get(
        f"/api/v1/orgs/{org_id}/users",
        headers={"X-User": "carol@gamma.test", "X-Role": "org_admin"},
    )
    assert r4.status_code == 200
    users = {row["email"]: row["role"] for row in r4.json()}
    assert users["carol@gamma.test"] == "org_admin"
    assert users["dave@gamma.test"] == "viewer"


def test_org_admin_cannot_assign_org_admin_role(client: TestClient, db):
    from app.services import auth_service

    auth_service.create_user(db, email="eve@delta.test", password="testpass12", display_name="Eve")
    org = Organization(name="Delta", slug="delta")
    db.add(org)
    db.commit()
    db.refresh(org)
    db.add(OrgMembership(organization_id=org.id, user_identifier="e@delta.test", role="org_admin"))
    db.commit()

    r = client.post(
        f"/api/v1/orgs/{org.id}/users",
        json={"email": "eve@delta.test", "role": "org_admin"},
        headers={"X-User": "e@delta.test", "X-Role": "org_admin"},
    )
    assert r.status_code == 403


def test_org_admin_cannot_assign_root_admin(client: TestClient, db):
    from app.services import auth_service

    auth_service.create_user(db, email="bad@delta.test", password="testpass12", display_name="Bad")
    org = Organization(name="Delta2", slug="delta2")
    db.add(org)
    db.commit()
    db.refresh(org)
    db.add(OrgMembership(organization_id=org.id, user_identifier="e2@delta.test", role="org_admin"))
    db.commit()

    r = client.post(
        f"/api/v1/orgs/{org.id}/users",
        json={"email": "bad@delta.test", "role": "root_admin"},
        headers={"X-User": "e2@delta.test", "X-Role": "org_admin"},
    )
    assert r.status_code == 403


def test_patch_and_delete_member(client: TestClient, db):
    org = Organization(name="Epsilon", slug="epsilon")
    db.add(org)
    db.commit()
    db.refresh(org)
    db.add(OrgMembership(organization_id=org.id, user_identifier="f", role="org_admin"))
    db.add(OrgMembership(organization_id=org.id, user_identifier="g", role="viewer"))
    db.commit()

    r = client.patch(
        f"/api/v1/orgs/{org.id}/users/g",
        json={"role": "approver"},
        headers={"X-User": "f", "X-Role": "org_admin"},
    )
    assert r.status_code == 200
    assert r.json()["role"] == "approver"

    r2 = client.delete(
        f"/api/v1/orgs/{org.id}/users/g",
        headers={"X-User": "f", "X-Role": "org_admin"},
    )
    assert r2.status_code == 204


def test_tenant_table_works_alongside_organizations(db):
    """Ensure Tenant ORM works when organizations table also exists."""
    org = Organization(name="Linked", slug="linked")
    db.add(org)
    db.commit()
    t = Tenant(name=f"tenant-{uuid4()}")
    db.add(t)
    db.commit()
    db.refresh(t)
    assert t.id is not None


def test_msp_admin_lists_orgs_and_adds_user(client: TestClient, db):
    from app.services import auth_service

    auth_service.create_user(db, email="msp@msp.test", password="testpass12", display_name="MSP")
    auth_service.create_user(db, email="end@msp.test", password="testpass12", display_name="End")
    org = Organization(name="MSP Org", slug="msp-org")
    db.add(org)
    db.commit()
    db.refresh(org)
    msp = auth_service.get_user_by_email(db, "msp@msp.test")
    db.add(
        OrgMembership(
            organization_id=org.id,
            user_id=msp.id,
            user_identifier=msp.email,
            role="admin",
        )
    )
    db.commit()

    headers = {"X-User": msp.email, "X-Role": "admin", "X-Current-Organization-Id": str(org.id)}
    r = client.get("/api/v1/orgs", headers=headers)
    assert r.status_code == 200
    assert any(row["id"] == str(org.id) for row in r.json())

    r2 = client.post(
        f"/api/v1/orgs/{org.id}/users",
        json={"email": "end@msp.test", "role": "viewer"},
        headers=headers,
    )
    assert r2.status_code == 201
    assert r2.json()["role"] == "viewer"


def test_msp_admin_cannot_assign_org_admin(client: TestClient, db):
    from app.services import auth_service

    auth_service.create_user(db, email="u1@msp.test", password="testpass12", display_name="U1")
    auth_service.create_user(db, email="u2@msp.test", password="testpass12", display_name="U2")
    org = Organization(name="MSP Org2", slug="msp-org2")
    db.add(org)
    db.commit()
    db.refresh(org)
    msp = auth_service.get_user_by_email(db, "u1@msp.test")
    db.add(
        OrgMembership(
            organization_id=org.id,
            user_id=msp.id,
            user_identifier=msp.email,
            role="admin",
        )
    )
    db.commit()
    headers = {"X-User": msp.email, "X-Role": "admin", "X-Current-Organization-Id": str(org.id)}
    r = client.post(
        f"/api/v1/orgs/{org.id}/users",
        json={"email": "u2@msp.test", "role": "org_admin"},
        headers=headers,
    )
    assert r.status_code == 403


def test_msp_admin_cannot_remove_org_admin(client: TestClient, db):
    org = Organization(name="MSP Org3", slug="msp-org3")
    db.add(org)
    db.commit()
    db.refresh(org)
    db.add(OrgMembership(organization_id=org.id, user_identifier="owner@msp.test", role="org_admin"))
    db.add(OrgMembership(organization_id=org.id, user_identifier="mspops@msp.test", role="admin"))
    db.commit()

    r = client.delete(
        f"/api/v1/orgs/{org.id}/users/owner@msp.test",
        headers={"X-User": "mspops@msp.test", "X-Role": "admin", "X-Current-Organization-Id": str(org.id)},
    )
    assert r.status_code == 403
