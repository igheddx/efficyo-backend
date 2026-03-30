"""OIDC SSO (OAuth2 authorization code). Local password login remains on POST /api/v1/login."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.services import auth_service
from app.services.oidc_flow import (
    OIDC_STATE_COOKIE,
    OIDC_STATE_MAX_AGE_SEC,
    complete_oidc_login,
    is_oidc_configured,
    build_authorization_redirect_url,
)

router = APIRouter(prefix="/auth", tags=["auth-oidc"])


def _redirect_with_oidc_error(message: str) -> RedirectResponse:
    target = settings.oidc_post_login_redirect
    q = urlencode({"oidc_error": message[:400]})
    joiner = "&" if "?" in target else "?"
    r = RedirectResponse(
        url=f"{target}{joiner}{q}",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )
    r.delete_cookie(OIDC_STATE_COOKIE, path="/")
    return r


@router.get("/oidc/login")
def oidc_login_start() -> RedirectResponse:
    if not is_oidc_configured():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OIDC SSO is not configured (set FPTNEXT_OIDC_ISSUER_URL, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI).",
        )
    url, cookie_val = build_authorization_redirect_url()
    r = RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    r.set_cookie(
        key=OIDC_STATE_COOKIE,
        value=cookie_val,
        max_age=OIDC_STATE_MAX_AGE_SEC,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return r


@router.get("/oidc/callback")
def oidc_login_callback(
    request: Request,
    db_session: Session = Depends(get_db),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> RedirectResponse:
    if not is_oidc_configured():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OIDC SSO is not configured.",
        )

    if error:
        msg = error_description or error
        return _redirect_with_oidc_error(msg or "OIDC authorization failed")

    state_cookie = request.cookies.get(OIDC_STATE_COOKIE)
    try:
        user = complete_oidc_login(
            db_session,
            code=code,
            state=state,
            state_cookie=state_cookie,
        )
    except HTTPException as exc:
        detail = exc.detail
        text = detail if isinstance(detail, str) else str(detail)
        return _redirect_with_oidc_error(text or "OIDC login failed")

    if (user.status or "active") != "active":
        return _redirect_with_oidc_error("This account has been disabled.")

    from app.services import context_defaults_service

    org_id = context_defaults_service.pick_initial_session_organization_id(db_session, user)
    _, raw = auth_service.create_session(db_session, user, current_organization_id=org_id)
    user.last_login_at = datetime.now(timezone.utc)
    db_session.add(user)
    db_session.commit()

    r = RedirectResponse(
        settings.oidc_post_login_redirect,
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )
    r.delete_cookie(OIDC_STATE_COOKIE, path="/")
    auth_service.set_session_cookie(r, raw)
    return r
