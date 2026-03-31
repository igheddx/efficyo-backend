"""Multi-approver approval requests."""

from app.models.access_grant import AccessGrant
from app.models.organization import OrgMembership
from app.services import auth_service

from tests.test_approvals_api import _seed_rec_for_approval


def _add_approver(db, org, email: str, role: str = "approver"):
    u = auth_service.create_user(
        db,
        email=email,
        password="testpass12",
        display_name=email.split("@")[0],
        is_root_admin=False,
    )
    db.add(
        OrgMembership(
            organization_id=org.id,
            user_id=u.id,
            user_identifier=u.email,
            role=role,
        )
    )
    db.commit()
    db.refresh(u)
    return u


def test_create_two_step_approve_completes_outcome(client, db, dev_org_scope_admin):
    org = dev_org_scope_admin["org"]
    admin_headers = dev_org_scope_admin["headers"]
    _t, ca, rec = _seed_rec_for_approval(db, org)
    ap1 = _add_approver(db, org, "ap1-approval@test.local", "approver")
    ap2 = _add_approver(db, org, "ap2-approval@test.local", "approver")

    body = {
        "recommendation_id": str(rec.id),
        "organization_id": str(org.id),
        "cloud_account_id": str(ca.id),
        "approver_user_ids": [str(ap1.id), str(ap2.id)],
        "execution_owner_user_id": str(ap1.id),
        "approval_mode": "all_required",
    }
    r = client.post("/api/v1/approval-requests", headers=admin_headers, json=body)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["status"] == "submitted"
    assert data["approvals_required"] == 2
    assert data["execution_owner_user_id"] == str(ap1.id)
    req_id = data["id"]

    h1 = {
        "X-User": ap1.email,
        "X-Role": "approver",
        "X-Current-Organization-Id": str(org.id),
    }
    r1 = client.post(f"/api/v1/approval-requests/{req_id}/approve", headers=h1, json={"comment": "ok from 1"})
    assert r1.status_code == 200, r1.text
    assert r1.json()["status"] == "partially_approved"

    url = f"/api/v1/tenants/{_t.id}/cloud-accounts/{ca.id}/recommendations/{rec.id}/approve"
    r_conflict = client.post(url, headers=h1, json={})
    assert r_conflict.status_code == 409

    h2 = {
        "X-User": ap2.email,
        "X-Role": "approver",
        "X-Current-Organization-Id": str(org.id),
    }
    r2 = client.post(f"/api/v1/approval-requests/{req_id}/approve", headers=h2, json={})
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "approved"
    assert r2.json()["approved_at"] is not None


def test_reject_stops_flow(client, db, dev_org_scope_admin):
    org = dev_org_scope_admin["org"]
    admin_headers = dev_org_scope_admin["headers"]
    _t, ca, rec = _seed_rec_for_approval(db, org)
    ap1 = _add_approver(db, org, "rj1@test.local", "approver")
    ap2 = _add_approver(db, org, "rj2@test.local", "approver")

    body = {
        "recommendation_id": str(rec.id),
        "organization_id": str(org.id),
        "cloud_account_id": str(ca.id),
        "approver_user_ids": [str(ap1.id), str(ap2.id)],
        "execution_owner_user_id": str(ap2.id),
    }
    r = client.post("/api/v1/approval-requests", headers=admin_headers, json=body)
    assert r.status_code == 201
    req_id = r.json()["id"]

    h1 = {
        "X-User": ap1.email,
        "X-Role": "approver",
        "X-Current-Organization-Id": str(org.id),
    }
    rj = client.post(
        f"/api/v1/approval-requests/{req_id}/reject",
        headers=h1,
        json={"comment": "no budget"},
    )
    assert rj.status_code == 200
    assert rj.json()["status"] == "rejected"


def test_eligible_approvers_excludes_viewer_grant_only(client, db, dev_org_scope_admin):
    """Submit-for-approval picker must not list users with only viewer operational access."""
    org = dev_org_scope_admin["org"]
    admin_headers = dev_org_scope_admin["headers"]
    t, ca, _rec = _seed_rec_for_approval(db, org)

    viewer_only = _add_approver(db, org, "view-only-elig@test.local", "member")
    can_approve = _add_approver(db, org, "can-approve-elig@test.local", "member")
    db.add(
        AccessGrant(
            organization_id=org.id,
            user_id=viewer_only.id,
            tenant_id=t.id,
            cloud_account_id=None,
            access_role="viewer",
        )
    )
    db.add(
        AccessGrant(
            organization_id=org.id,
            user_id=can_approve.id,
            tenant_id=t.id,
            cloud_account_id=None,
            access_role="approver",
        )
    )
    db.commit()

    r = client.get(
        f"/api/v1/approval-requests/eligible-approvers?tenant_id={t.id}&cloud_account_id={ca.id}",
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    emails = {row["email"] for row in r.json()}
    assert "can-approve-elig@test.local" in emails
    assert "view-only-elig@test.local" not in emails
    for row in r.json():
        assert row["effective_access_role"] in ("approver", "admin")


def test_eligible_approvers_legacy_admin_membership_with_viewer_grant(client, db, dev_org_scope_admin):
    """
    MSP ``admin`` membership must still count as operational admin when the user also has a viewer grant
    (otherwise the picker is empty despite org_service treating them as org managers).
    """
    org = dev_org_scope_admin["org"]
    admin_user = dev_org_scope_admin["user"]
    admin_headers = dev_org_scope_admin["headers"]
    t, ca, _rec = _seed_rec_for_approval(db, org)
    db.add(
        AccessGrant(
            organization_id=org.id,
            user_id=admin_user.id,
            tenant_id=t.id,
            cloud_account_id=None,
            access_role="viewer",
        )
    )
    db.commit()

    r = client.get(
        f"/api/v1/approval-requests/eligible-approvers?tenant_id={t.id}&cloud_account_id={ca.id}",
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    row = next((x for x in r.json() if x["email"] == admin_user.email), None)
    assert row is not None, r.json()
    assert row["effective_access_role"] == "admin"


def test_create_allows_execution_owner_not_in_selected_approvers(client, db, dev_org_scope_admin):
    org = dev_org_scope_admin["org"]
    admin_headers = dev_org_scope_admin["headers"]
    _t, ca, rec = _seed_rec_for_approval(db, org)
    ap1 = _add_approver(db, org, "owner-check-a@test.local", "approver")
    ap2 = _add_approver(db, org, "owner-check-b@test.local", "approver")
    ap3 = _add_approver(db, org, "owner-check-c@test.local", "approver")

    body = {
        "recommendation_id": str(rec.id),
        "organization_id": str(org.id),
        "cloud_account_id": str(ca.id),
        "approver_user_ids": [str(ap1.id), str(ap2.id)],
        "execution_owner_user_id": str(ap3.id),
        "approval_mode": "all_required",
    }
    r = client.post("/api/v1/approval-requests", headers=admin_headers, json=body)
    assert r.status_code == 201, r.text
    payload = r.json()
    assert payload["execution_owner_user_id"] == str(ap3.id)


def test_create_requires_execution_owner_field(client, db, dev_org_scope_admin):
    org = dev_org_scope_admin["org"]
    admin_headers = dev_org_scope_admin["headers"]
    _t, ca, rec = _seed_rec_for_approval(db, org)
    ap1 = _add_approver(db, org, "owner-required@test.local", "approver")
    ap2 = _add_approver(db, org, "owner-required-2@test.local", "approver")

    body = {
        "recommendation_id": str(rec.id),
        "organization_id": str(org.id),
        "cloud_account_id": str(ca.id),
        "approver_user_ids": [str(ap1.id), str(ap2.id)],
        "approval_mode": "all_required",
    }
    r = client.post("/api/v1/approval-requests", headers=admin_headers, json=body)
    assert r.status_code == 422, r.text
    assert "execution_owner_user_id" in r.text
