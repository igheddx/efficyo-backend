from __future__ import annotations

import hashlib
import secrets
import string
from datetime import datetime, timedelta, timezone

from app.core.org_slug import ensure_unique_org_slug, slugify_name
from uuid import UUID

from fastapi import HTTPException, Response, status
from passlib.hash import bcrypt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import DEMO_ORG_NAME
from app.core.password_policy import password_policy_error
from app.models.organization import Organization, OrgMembership
from app.models.user import AuthSession, User
from app.services import context_defaults_service

SESSION_TOKEN_BYTES = 32
TEMP_PASSWORD_EXPIRES_DAYS = 14


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _password_policy_error(password: str) -> str | None:
    return password_policy_error(password)


def generate_temporary_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if _password_policy_error(pw) is None:
            return pw


def verify_password(plain: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.verify(plain, password_hash)
    except ValueError:
        return False


def hash_password(plain: str) -> str:
    return bcrypt.hash(plain)


def get_user_by_email(db: Session, email: str) -> User | None:
    normalized = email.strip().lower()
    if not normalized:
        return None
    return db.query(User).filter(User.email == normalized).first()


def get_user_by_external_subject_id(db: Session, subject: str) -> User | None:
    sub = subject.strip()
    if not sub:
        return None
    return db.query(User).filter(User.external_subject_id == sub).first()


def create_user(
    db: Session,
    *,
    email: str,
    password: str,
    display_name: str,
    is_root_admin: bool = False,
    auth_provider: str = "local",
    external_subject_id: str | None = None,
) -> User:
    normalized = email.strip().lower()
    u = User(
        email=normalized,
        password_hash=hash_password(password),
        display_name=display_name.strip() or normalized,
        is_root_admin=is_root_admin,
        auth_provider=auth_provider,
        external_subject_id=external_subject_id,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def create_pending_local_user(
    db: Session,
    *,
    email: str,
    display_name: str,
    temporary_password: str,
    expires_in_days: int = TEMP_PASSWORD_EXPIRES_DAYS,
) -> User:
    normalized = email.strip().lower()
    now = datetime.now(timezone.utc)
    user = User(
        email=normalized,
        password_hash=hash_password(temporary_password),
        display_name=display_name.strip() or normalized,
        status="pending",
        must_change_password=True,
        temporary_password_expires_at=now + timedelta(days=expires_in_days),
        is_root_admin=False,
        auth_provider="local",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def rotate_temporary_password_for_existing_local_user(
    db: Session,
    *,
    user: User,
    temporary_password: str,
    expires_in_days: int = TEMP_PASSWORD_EXPIRES_DAYS,
) -> User:
    now = datetime.now(timezone.utc)
    user.password_hash = hash_password(temporary_password)
    user.must_change_password = True
    user.temporary_password_expires_at = now + timedelta(days=expires_in_days)
    if hasattr(user, "status"):
        user.status = "pending"
    db.commit()
    db.refresh(user)
    return user


def create_session(
    db: Session,
    user: User,
    *,
    current_organization_id: UUID | None = None,
) -> tuple[AuthSession, str]:
    raw = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
    token_hash = _hash_token(raw)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=settings.session_ttl_hours)
    row = AuthSession(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires,
        current_organization_id=current_organization_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, raw


def get_session_by_raw_token(db: Session, raw_token: str) -> AuthSession | None:
    if not raw_token:
        return None
    th = _hash_token(raw_token)
    row = db.query(AuthSession).filter(AuthSession.token_hash == th).first()
    if row is None:
        return None
    if row.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        db.delete(row)
        db.commit()
        return None
    return row


def delete_session(db: Session, row: AuthSession) -> None:
    db.delete(row)
    db.commit()


def set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=settings.session_cookie_name, path="/")


def pick_default_organization_id(db: Session, user: User) -> UUID | None:
    rows = (
        db.query(OrgMembership.organization_id)
        .join(Organization, Organization.id == OrgMembership.organization_id)
        .filter(OrgMembership.user_id == user.id)
        .filter(Organization.status == "active")
        .distinct()
        .all()
    )
    ids = [r[0] for r in rows]
    if len(ids) == 1:
        return ids[0]
    return None


def login_with_password(db: Session, login: str, password: str) -> tuple[User, str]:
    """Returns (user, raw_session_token). Raises HTTPException on failure."""
    u = get_user_by_email(db, login)
    if u is None or not verify_password(password, u.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    if u.auth_provider == "local" and u.must_change_password:
        exp = u.temporary_password_expires_at
        if exp is None or exp.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "temporary_password_expired",
                    "message": "Your temporary password has expired. Ask your administrator to send a new invitation.",
                },
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "temporary_password_change_required",
                "message": "Temporary password accepted. Set a new password to finish sign in.",
            },
        )

    if (u.status or "active") != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been disabled.",
        )

    org_id = context_defaults_service.pick_initial_session_organization_id(db, u)
    _session, raw = create_session(db, u, current_organization_id=org_id)
    now = datetime.now(timezone.utc)
    u.last_login_at = now
    db.add(u)
    db.commit()
    db.refresh(u)
    return u, raw


def complete_temporary_password_login(
    db: Session,
    *,
    login: str,
    temporary_password: str,
    new_password: str,
    confirm_password: str,
) -> tuple[User, str]:
    u = get_user_by_email(db, login)
    if u is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")
    if (u.auth_provider or "local") != "local":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password onboarding applies only to local MEEZI accounts.",
        )
    if not u.must_change_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Temporary password flow is not active.")
    if not verify_password(temporary_password, u.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")

    exp = u.temporary_password_expires_at
    if exp is None or exp.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Temporary password expired. Ask your administrator to send a new invitation.",
        )

    if new_password != confirm_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "password_confirmation_mismatch",
                "field": "confirm_password",
                "message": "Passwords do not match.",
            },
        )

    policy_error = _password_policy_error(new_password)
    if policy_error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "password_policy_failed",
                "field": "new_password",
                "message": policy_error,
            },
        )

    u.password_hash = hash_password(new_password)
    u.status = "active"
    u.must_change_password = False
    u.temporary_password_expires_at = None
    u.last_login_at = datetime.now(timezone.utc)
    db.add(u)
    db.commit()
    db.refresh(u)

    org_id = context_defaults_service.pick_initial_session_organization_id(db, u)
    _session, raw = create_session(db, u, current_organization_id=org_id)
    return u, raw


def ensure_local_seed_users(db: Session) -> None:
    """
    Idempotent dev/demo users and demo org memberships (see DEMO_ORG_NAME).
    Safe to call on every app startup in dev.
    """
    if not settings.enable_demo_and_local_seed:
        return
    demo_org = db.query(Organization).filter(Organization.name == DEMO_ORG_NAME).first()
    if demo_org is None:
        legacy = db.query(Organization).filter(Organization.name == "Demo Organization").first()
        if legacy is not None:
            legacy.name = DEMO_ORG_NAME
            db.commit()
            db.refresh(legacy)
            demo_org = legacy
        else:
            slug_base = slugify_name(DEMO_ORG_NAME)
            demo_org = Organization(
                name=DEMO_ORG_NAME,
                slug=ensure_unique_org_slug(db, slug_base),
                status="active",
            )
            db.add(demo_org)
            db.commit()
            db.refresh(demo_org)

    pw = settings.dev_seed_password

    def _ensure_user(
        email: str,
        display_name: str,
        *,
        is_root: bool = False,
        org_role: str | None = None,
    ) -> None:
        u = get_user_by_email(db, email)
        if u is None:
            u = User(
                email=email.strip().lower(),
                password_hash=hash_password(pw),
                display_name=display_name,
                is_root_admin=is_root,
                auth_provider="local",
            )
            db.add(u)
            db.flush()
        else:
            if is_root and not u.is_root_admin:
                u.is_root_admin = True
        if org_role:
            exists = (
                db.query(OrgMembership.id)
                .filter(
                    OrgMembership.organization_id == demo_org.id,
                    OrgMembership.user_id == u.id,
                )
                .first()
            )
            if not exists:
                db.add(
                    OrgMembership(
                        organization_id=demo_org.id,
                        user_id=u.id,
                        user_identifier=u.email,
                        role=org_role,
                    )
                )
        db.commit()

    # Create production seed user if configured (overrides demo users)
    if (
        settings.prod_seed_email
        and settings.prod_seed_name
        and settings.prod_seed_password
    ):
        # Use custom production seed user (with custom password)
        prod_pw = settings.prod_seed_password

        def _ensure_prod_user(
            email: str,
            display_name: str,
            password: str,
            *,
            is_root: bool = False,
            org_role: str | None = None,
        ) -> None:
            u = get_user_by_email(db, email)
            if u is None:
                u = User(
                    email=email.strip().lower(),
                    password_hash=hash_password(password),
                    display_name=display_name,
                    is_root_admin=is_root,
                    auth_provider="local",
                )
                db.add(u)
                db.flush()
            else:
                if is_root and not u.is_root_admin:
                    u.is_root_admin = True
            if org_role:
                exists = (
                    db.query(OrgMembership.id)
                    .filter(
                        OrgMembership.organization_id == demo_org.id,
                        OrgMembership.user_id == u.id,
                    )
                    .first()
                )
                if not exists:
                    db.add(
                        OrgMembership(
                            organization_id=demo_org.id,
                            user_id=u.id,
                            user_identifier=u.email,
                            role=org_role,
                        )
                    )
            db.commit()

        _ensure_prod_user(
            settings.prod_seed_email,
            settings.prod_seed_name,
            prod_pw,
            is_root=True,
            org_role="root_admin",
        )
    else:
        # Use demo seed users (default)
        _ensure_user("root@fptnext.local", "Platform Root", is_root=True, org_role="root_admin")
        _ensure_user("demo@fptnext.local", "Demo User", org_role="org_admin")
        _ensure_user("admin@fptnext.local", "Tenant Admin", org_role="admin")
        _ensure_user("approver@fptnext.local", "Approver", org_role="approver")
        _ensure_user("viewer@fptnext.local", "Viewer", org_role="viewer")
