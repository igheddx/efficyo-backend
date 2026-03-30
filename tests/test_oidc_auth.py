"""OIDC routes (disabled by default; redirect when configured is mocked)."""

from fastapi.testclient import TestClient


def test_oidc_login_not_configured_returns_404(client: TestClient):
    r = client.get("/api/v1/auth/oidc/login", follow_redirects=False)
    assert r.status_code == 404


def test_oidc_callback_not_configured_returns_404(client: TestClient):
    r = client.get("/api/v1/auth/oidc/callback", follow_redirects=False)
    assert r.status_code == 404


def test_oidc_login_redirect_when_configured(client: TestClient, monkeypatch):
    monkeypatch.setattr("app.api.v1.auth_oidc.is_oidc_configured", lambda: True)
    monkeypatch.setattr(
        "app.api.v1.auth_oidc.build_authorization_redirect_url",
        lambda: ("https://idp.example/oauth2/authorize?client_id=x", "test-state-cookie-value"),
    )
    r = client.get("/api/v1/auth/oidc/login", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"].startswith("https://idp.example/oauth2/authorize")
    assert r.cookies.get("fptnext_oidc_state") == "test-state-cookie-value"


def test_extract_email_from_claims():
    from app.services.oidc_flow import extract_email, extract_display_name

    assert extract_email({"email": "A@Example.COM"}) == "a@example.com"
    assert extract_email({"emails": ["b@test.dev"]}) == "b@test.dev"
    assert extract_email({"preferred_username": "c@test.dev"}) == "c@test.dev"
    assert extract_email({"sub": "x"}) is None
    assert extract_display_name({"name": " Pat "}, "x@y.z") == "Pat"
    assert extract_display_name({}, "x@y.z") == "x"
