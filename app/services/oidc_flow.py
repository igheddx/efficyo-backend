"""
OIDC authorization-code login: discovery, state cookie, token exchange, ID token validation.

Uses the same `User` / `AuthSession` model as local password login. Configure via FPTNEXT_OIDC_* env vars.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import HTTPException, status
from jwt import PyJWKClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.user import User
from app.services import auth_service

OIDC_STATE_COOKIE = "fptnext_oidc_state"
OIDC_STATE_MAX_AGE_SEC = 600

_disc_cache: dict[str, Any] | None = None
_disc_issuer_key: str | None = None


def reset_oidc_discovery_cache() -> None:
    """Test hook: clear cached OpenID configuration."""
    global _disc_cache, _disc_issuer_key
    _disc_cache = None
    _disc_issuer_key = None


def is_oidc_configured() -> bool:
    return bool(
        settings.oidc_issuer_url
        and settings.oidc_client_id
        and settings.oidc_client_secret
        and settings.oidc_redirect_uri
    )


def _signing_secret() -> str:
    return settings.oidc_client_secret or ""


def sign_oidc_state_cookie(payload: dict[str, Any]) -> str:
    secret = _signing_secret()
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    sig = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    sig_s = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return f"{body}.{sig_s}"


def _b64url_pad(s: str) -> str:
    return s + "=" * (-len(s) % 4)


def verify_oidc_state_cookie(token: str, *, max_age_sec: int = OIDC_STATE_MAX_AGE_SEC) -> dict[str, Any]:
    secret = _signing_secret()
    try:
        body_b64, sig_s = token.split(".", 1)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid OIDC state") from e

    expected_sig = hmac.new(secret.encode("utf-8"), body_b64.encode("ascii"), hashlib.sha256).digest()
    try:
        got_sig = base64.urlsafe_b64decode(_b64url_pad(sig_s))
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid OIDC state") from e
    if not hmac.compare_digest(expected_sig, got_sig):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid OIDC state signature")

    try:
        data = json.loads(base64.urlsafe_b64decode(_b64url_pad(body_b64)).decode("utf-8"))
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid OIDC state payload") from e

    iat = int(data.get("iat") or 0)
    if not iat or time.time() - iat > max_age_sec:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="OIDC state expired")
    return data


def fetch_openid_configuration() -> dict[str, Any]:
    global _disc_cache, _disc_issuer_key
    if not settings.oidc_issuer_url:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="OIDC is not configured")
    issuer = settings.oidc_issuer_url.rstrip("/")
    if _disc_cache is not None and _disc_issuer_key == issuer:
        return _disc_cache
    url = f"{issuer}/.well-known/openid-configuration"
    with httpx.Client(timeout=20.0) as client:
        r = client.get(url)
        if r.status_code >= 400:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"OIDC discovery failed ({r.status_code})",
            )
        _disc_cache = r.json()
        _disc_issuer_key = issuer
        return _disc_cache


def build_authorization_redirect_url() -> tuple[str, str]:
    """Return (Location URL for browser, value for OIDC state cookie)."""
    cfg = fetch_openid_configuration()
    auth_ep = cfg.get("authorization_endpoint")
    if not auth_ep or not isinstance(auth_ep, str):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC discovery missing authorization_endpoint",
        )
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(24)
    payload = {"state": state, "nonce": nonce, "iat": int(time.time())}
    cookie_val = sign_oidc_state_cookie(payload)

    q = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": settings.oidc_redirect_uri,
        "scope": settings.oidc_scopes,
        "state": state,
        "nonce": nonce,
    }
    return f"{auth_ep}?{urlencode(q)}", cookie_val


def exchange_code_for_tokens(code: str) -> dict[str, Any]:
    cfg = fetch_openid_configuration()
    token_ep = cfg.get("token_endpoint")
    if not token_ep or not isinstance(token_ep, str):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC discovery missing token_endpoint",
        )
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.oidc_redirect_uri,
        "client_id": settings.oidc_client_id,
        "client_secret": settings.oidc_client_secret,
    }
    with httpx.Client(timeout=20.0) as client:
        r = client.post(token_ep, data=data, headers={"Accept": "application/json"})
        if r.status_code >= 400:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                detail=f"Token endpoint error: {r.status_code}",
            )
        return r.json()


def decode_id_token(id_token: str, expected_nonce: str) -> dict[str, Any]:
    cfg = fetch_openid_configuration()
    jwks_uri = cfg.get("jwks_uri")
    issuer = cfg.get("issuer")
    if not jwks_uri or not issuer:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC discovery missing jwks_uri or issuer",
        )
    jwks_client = PyJWKClient(jwks_uri)
    signing_key = jwks_client.get_signing_key_from_jwt(id_token)
    supported = cfg.get("id_token_signing_alg_values_supported")
    if isinstance(supported, list) and supported:
        algorithms = [
            str(a)
            for a in supported
            if str(a) in ("RS256", "RS384", "RS512", "ES256", "ES384", "PS256")
        ]
    else:
        algorithms = ["RS256", "RS384", "RS512", "ES256"]
    if not algorithms:
        algorithms = ["RS256"]
    claims = jwt.decode(
        id_token,
        signing_key.key,
        algorithms=algorithms,
        issuer=issuer,
        options={"require": ["exp", "sub"], "verify_aud": False},
    )
    cid = settings.oidc_client_id
    aud = claims.get("aud")
    aud_ok = aud == cid or (isinstance(aud, list) and cid in aud)
    if not aud_ok:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="ID token audience does not match this application client_id",
        )
    if claims.get("nonce") != expected_nonce:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Invalid ID token nonce",
        )
    return claims


def extract_email(claims: dict[str, Any]) -> str | None:
    email = claims.get("email")
    if isinstance(email, str) and email.strip():
        return email.strip().lower()
    emails = claims.get("emails")
    if isinstance(emails, list) and emails:
        first = emails[0]
        if isinstance(first, str) and first.strip():
            return first.strip().lower()
    pu = claims.get("preferred_username")
    if isinstance(pu, str) and "@" in pu:
        return pu.strip().lower()
    return None


def extract_display_name(claims: dict[str, Any], email: str) -> str:
    name = claims.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    gn = claims.get("given_name")
    fn = claims.get("family_name")
    parts: list[str] = []
    if isinstance(gn, str) and gn.strip():
        parts.append(gn.strip())
    if isinstance(fn, str) and fn.strip():
        parts.append(fn.strip())
    if parts:
        return " ".join(parts)
    return email.split("@", 1)[0] if email else "User"


def resolve_or_update_oidc_user(db: Session, claims: dict[str, Any]) -> User:
    sub = str(claims.get("sub") or "").strip()
    if not sub:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="ID token missing sub")
    email = extract_email(claims)
    if not email:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="OIDC ID token did not include an email (configure scopes: openid email profile).",
        )
    display_name = extract_display_name(claims, email)

    u = auth_service.get_user_by_external_subject_id(db, sub)
    if u is not None:
        u.auth_provider = "oidc"
        u.external_subject_id = sub
        if u.email != email:
            taken = (
                db.query(User.id)
                .filter(User.email == email, User.id != u.id)
                .first()
            )
            if taken is not None:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail="Email from identity provider is already used by another account.",
                )
            u.email = email
        u.display_name = display_name or u.display_name
        db.commit()
        db.refresh(u)
        return u

    u = auth_service.get_user_by_email(db, email)
    if u is not None:
        if u.external_subject_id and u.external_subject_id != sub:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="This email is already linked to a different external identity.",
            )
        u.external_subject_id = sub
        u.auth_provider = "oidc"
        u.display_name = display_name or u.display_name
        db.commit()
        db.refresh(u)
        return u

    row = User(
        email=email,
        password_hash=None,
        display_name=display_name,
        auth_provider="oidc",
        external_subject_id=sub,
        is_root_admin=False,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def complete_oidc_login(
    db: Session,
    *,
    code: str | None,
    state: str | None,
    state_cookie: str | None,
) -> User:
    if not code or not state:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Missing authorization code or state",
        )
    if not state_cookie:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Missing OIDC state cookie",
        )
    cookie_data = verify_oidc_state_cookie(state_cookie)
    if cookie_data.get("state") != state:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="OIDC state mismatch")
    nonce = cookie_data.get("nonce")
    if not nonce:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid OIDC state")

    tokens = exchange_code_for_tokens(code)
    id_token = tokens.get("id_token")
    if not id_token or not isinstance(id_token, str):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Token response missing id_token",
        )
    claims = decode_id_token(id_token, str(nonce))
    return resolve_or_update_oidc_user(db, claims)
