"""Platform root control plane (/api/v1/root/*)."""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.models.organization import OrgMembership, Organization
from app.services import auth_service


def test_root_routes_forbidden_for_viewer(client: TestClient):
    r = client.get("/api/v1/root/dashboard", headers={"X-User": "u1", "X-Role": "viewer"})
    assert r.status_code == 403


def test_root_dashboard_ok(client: TestClient):
    r = client.get("/api/v1/root/dashboard", headers={"X-User": "root", "X-Role": "root_admin"})
    assert r.status_code == 200
    data = r.json()
    assert "total_organizations" in data
    assert "pending_approvals" in data


def test_root_org_list_pagination(client: TestClient, db):
    for i in range(3):
        db.add(Organization(name=f"RootList{i}", slug=f"root-list-{i}-{uuid4().hex[:6]}"))
    db.commit()

    r = client.get(
        "/api/v1/root/organizations?page=1&page_size=2",
        headers={"X-User": "root", "X-Role": "root_admin"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert body["total"] >= 3
    assert len(body["items"]) == 2


def test_root_create_org_and_status(client: TestClient, db):
    r = client.post(
        "/api/v1/root/organizations",
        json={"name": "RootCreated", "slug": "root-created-unique"},
        headers={"X-User": "root", "X-Role": "root_admin"},
    )
    assert r.status_code == 201
    oid = r.json()["id"]
    assert r.json()["slug"] == "root-created-unique"
    assert r.json()["status"] == "active"

    r2 = client.patch(
        f"/api/v1/root/organizations/{oid}",
        json={"status": "disabled"},
        headers={"X-User": "root", "X-Role": "root_admin"},
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "disabled"


def test_root_duplicate_slug_conflict(client: TestClient, db):
    db.add(Organization(name="A", slug="slug-dup-test"))
    db.commit()
    r = client.post(
        "/api/v1/root/organizations",
        json={"name": "B", "slug": "slug-dup-test"},
        headers={"X-User": "root", "X-Role": "root_admin"},
    )
    assert r.status_code == 409


def test_root_create_user_and_list(client: TestClient, db):
    org = Organization(name="OrgForRootUser", slug="org-for-root-user")
    db.add(org)
    db.commit()
    db.refresh(org)

    r = client.post(
        "/api/v1/root/users",
        json={
            "organization_id": str(org.id),
            "email": "rootuser@example.test",
            "password": "testpass12",
            "display_name": "Root User",
            "role": "viewer",
        },
        headers={"X-User": "root", "X-Role": "root_admin"},
    )
    assert r.status_code == 201
    assert r.json()["email"] == "rootuser@example.test"
    assert r.json()["role"] == "viewer"

    r2 = client.get("/api/v1/root/users", headers={"X-User": "root", "X-Role": "root_admin"})
    assert r2.status_code == 200
    emails = {row["email"] for row in r2.json()["items"]}
    assert "rootuser@example.test" in emails


def test_root_rejects_admin_role_on_post_users(client: TestClient, db):
    org = Organization(name="O", slug="o-root-reject")
    db.add(org)
    db.commit()
    db.refresh(org)
    r = client.post(
        "/api/v1/root/users",
        json={
            "organization_id": str(org.id),
            "email": "x@y.test",
            "password": "testpass12",
            "role": "admin",
        },
        headers={"X-User": "root", "X-Role": "root_admin"},
    )
    assert r.status_code == 422


def test_root_user_status_patch(client: TestClient, db):
    org = Organization(name="O2", slug="o2-root-status")
    db.add(org)
    db.commit()
    db.refresh(org)
    u = auth_service.create_user(db, email="status@t.test", password="testpass12", display_name="S")
    db.add(OrgMembership(organization_id=org.id, user_id=u.id, user_identifier=u.email, role="viewer"))
    db.commit()

    r = client.patch(
        f"/api/v1/root/users/{u.id}/status",
        json={"status": "disabled"},
        headers={"X-User": "root", "X-Role": "root_admin"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "disabled"


def test_root_list_org_users(client: TestClient, db):
    org = Organization(name="O3", slug="o3-root-members")
    db.add(org)
    db.commit()
    db.refresh(org)
    u = auth_service.create_user(db, email="mem@t.test", password="testpass12", display_name="M")
    db.add(OrgMembership(organization_id=org.id, user_id=u.id, user_identifier=u.email, role="approver"))
    db.commit()

    r = client.get(
        f"/api/v1/root/organizations/{org.id}/users",
        headers={"X-User": "root", "X-Role": "root_admin"},
    )
    assert r.status_code == 200
    assert len(r.json()) >= 1
