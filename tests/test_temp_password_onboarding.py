from fastapi.testclient import TestClient

from app.models.organization import OrgMembership, Organization
from app.services import auth_service


def test_root_invite_flow_requires_password_change(client: TestClient, db, monkeypatch):
    org = Organization(name="Invite Org", slug="invite-org")
    db.add(org)
    db.commit()
    db.refresh(org)

    monkeypatch.setattr("app.services.org_service.auth_service.generate_temporary_password", lambda: "TempPass1!")
    sent = {}

    def _capture_email(**kwargs):
        sent.update(kwargs)

    monkeypatch.setattr("app.services.org_service.invite_email_service.send_local_user_invitation_email", _capture_email)

    invite = client.post(
        f"/api/v1/orgs/{org.id}/users",
        json={"email": "new.user@meezi.local", "role": "viewer", "display_name": "New User"},
        headers={"X-User": "root", "X-Role": "root_admin"},
    )
    assert invite.status_code == 201
    body = invite.json()
    assert body["email"] == "new.user@meezi.local"
    assert body["user_status"] == "pending"

    assert sent["recipient_email"] == "new.user@meezi.local"
    assert sent["recipient_name"] == "New User"
    assert sent["temporary_password"] == "TempPass1!"

    blocked_login = client.post(
        "/api/v1/login",
        json={"login": "new.user@meezi.local", "password": "TempPass1!"},
    )
    assert blocked_login.status_code == 403
    assert blocked_login.json().get("detail", {}).get("code") == "temporary_password_change_required"

    complete = client.post(
        "/api/v1/password/temporary/complete",
        json={
            "login": "new.user@meezi.local",
            "temporary_password": "TempPass1!",
            "new_password": "NewPass22!",
            "confirm_password": "NewPass22!",
        },
    )
    assert complete.status_code == 200

    user = auth_service.get_user_by_email(db, "new.user@meezi.local")
    assert user is not None
    assert user.status == "active"
    assert user.must_change_password is False
    assert user.temporary_password_expires_at is None

    login_ok = client.post(
        "/api/v1/login",
        json={"login": "new.user@meezi.local", "password": "NewPass22!"},
    )
    assert login_ok.status_code == 200


def test_temporary_password_completion_rejected_for_oidc_user(client: TestClient, db):
    auth_service.create_user(
        db,
        email="oidc.user@meezi.local",
        password="Ignored1!",
        display_name="OIDC User",
        auth_provider="oidc",
    )
    row = auth_service.get_user_by_email(db, "oidc.user@meezi.local")
    row.must_change_password = True
    db.add(row)
    db.commit()

    r = client.post(
        "/api/v1/password/temporary/complete",
        json={
            "login": "oidc.user@meezi.local",
            "temporary_password": "Ignored1!",
            "new_password": "NewPass22!",
            "confirm_password": "NewPass22!",
        },
    )
    assert r.status_code == 400
    assert "local MEEZI accounts" in r.text


def test_duplicate_member_add_does_not_resend_invite_email(client: TestClient, db, monkeypatch):
    org = Organization(name="Existing Member Org", slug="existing-member-org")
    db.add(org)
    db.commit()
    db.refresh(org)

    existing = auth_service.create_pending_local_user(
        db,
        email="existing.member@meezi.local",
        display_name="Existing Member",
        temporary_password="InitTemp1!",
    )
    db.add(
        OrgMembership(
            organization_id=org.id,
            user_id=existing.id,
            user_identifier=existing.email,
            role="viewer",
        )
    )
    db.commit()

    sent = {"count": 0}

    def _capture_email(**kwargs):
        sent["count"] += 1

    monkeypatch.setattr("app.services.org_service.invite_email_service.send_local_user_invitation_email", _capture_email)

    r = client.post(
        f"/api/v1/orgs/{org.id}/users",
        json={"email": existing.email, "role": "approver"},
        headers={"X-User": "root", "X-Role": "root_admin"},
    )

    assert r.status_code == 201
    assert r.json()["email"] == existing.email
    assert r.json()["role"] == "approver"
    assert sent["count"] == 0


def test_temporary_password_completion_rejects_password_shorter_than_10(client: TestClient, db, monkeypatch):
    org = Organization(name="Policy Org", slug="policy-org")
    db.add(org)
    db.commit()
    db.refresh(org)

    monkeypatch.setattr("app.services.org_service.auth_service.generate_temporary_password", lambda: "TempPass1!")
    monkeypatch.setattr("app.services.org_service.invite_email_service.send_local_user_invitation_email", lambda **_: None)

    invite = client.post(
        f"/api/v1/orgs/{org.id}/users",
        json={"email": "policy.user@meezi.local", "role": "viewer"},
        headers={"X-User": "root", "X-Role": "root_admin"},
    )
    assert invite.status_code == 201

    short = client.post(
        "/api/v1/password/temporary/complete",
        json={
            "login": "policy.user@meezi.local",
            "temporary_password": "TempPass1!",
            "new_password": "Short9!x",
            "confirm_password": "Short9!x",
        },
    )
    assert short.status_code == 400
    detail = short.json().get("detail", {})
    assert detail.get("field") == "new_password"
    assert detail.get("message") == "Use at least 10 characters."


def test_temporary_password_completion_accepts_10_chars_with_spaces_and_specials(client: TestClient, db, monkeypatch):
    org = Organization(name="Policy Org 2", slug="policy-org-2")
    db.add(org)
    db.commit()
    db.refresh(org)

    monkeypatch.setattr("app.services.org_service.auth_service.generate_temporary_password", lambda: "TempPass1!")
    monkeypatch.setattr("app.services.org_service.invite_email_service.send_local_user_invitation_email", lambda **_: None)

    invite = client.post(
        f"/api/v1/orgs/{org.id}/users",
        json={"email": "policy.user2@meezi.local", "role": "viewer"},
        headers={"X-User": "root", "X-Role": "root_admin"},
    )
    assert invite.status_code == 201

    complete = client.post(
        "/api/v1/password/temporary/complete",
        json={
            "login": "policy.user2@meezi.local",
            "temporary_password": "TempPass1!",
            "new_password": "Good pass!",
            "confirm_password": "Good pass!",
        },
    )
    assert complete.status_code == 200


def test_temporary_password_completion_rejects_password_longer_than_64(client: TestClient, db, monkeypatch):
    org = Organization(name="Policy Org 3", slug="policy-org-3")
    db.add(org)
    db.commit()
    db.refresh(org)

    monkeypatch.setattr("app.services.org_service.auth_service.generate_temporary_password", lambda: "TempPass1!")
    monkeypatch.setattr("app.services.org_service.invite_email_service.send_local_user_invitation_email", lambda **_: None)

    invite = client.post(
        f"/api/v1/orgs/{org.id}/users",
        json={"email": "policy.user3@meezi.local", "role": "viewer"},
        headers={"X-User": "root", "X-Role": "root_admin"},
    )
    assert invite.status_code == 201

    long_password = "A" * 65
    too_long = client.post(
        "/api/v1/password/temporary/complete",
        json={
            "login": "policy.user3@meezi.local",
            "temporary_password": "TempPass1!",
            "new_password": long_password,
            "confirm_password": long_password,
        },
    )
    assert too_long.status_code == 400
    detail = too_long.json().get("detail", {})
    assert detail.get("field") == "new_password"
    assert detail.get("message") == "Password cannot be longer than 64 characters."


def test_login_invalid_credentials_message_is_generic(client: TestClient, db):
    auth_service.create_user(
        db,
        email="generic.login@meezi.local",
        password="ValidPass1!",
        display_name="Generic Login",
    )

    wrong_password = client.post(
        "/api/v1/login",
        json={"login": "generic.login@meezi.local", "password": "WrongPass1!"},
    )
    assert wrong_password.status_code == 401
    assert wrong_password.json().get("detail") == "Invalid email or password."

    unknown_user = client.post(
        "/api/v1/login",
        json={"login": "unknown.user@meezi.local", "password": "WrongPass1!"},
    )
    assert unknown_user.status_code == 401
    assert unknown_user.json().get("detail") == "Invalid email or password."
