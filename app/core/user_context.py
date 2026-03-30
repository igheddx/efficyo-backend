from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.authz import VALID_ROLES, resolve_effective_org_role
from app.core.config import settings
from app.core.db import get_db
from app.models.organization import OrgMembership
from app.models.user import AuthSession, User
from app.services import auth_service

if TYPE_CHECKING:
    pass

Role = Literal["root_admin", "org_admin", "admin", "approver", "viewer", "member"]


@dataclass
class UserContext:
    """
    Request-scoped principal: authenticated identity + effective org role.

    Populated from the session cookie (identity) and `authz.resolve_effective_org_role`
    (authorization). The optional dev header path is isolated for tests only.
    """

    user_id: UUID | None
    email: str
    display_name: str
    role: Role
    current_organization_id: UUID | None
    session: AuthSession | None
    # When using X-User / X-Role dev fallback, match memberships on this string.
    legacy_membership_key: str | None = None
    # Platform operator: User.is_root_admin (session) or X-Role root_admin (dev-only tests).
    is_platform_root: bool = False


def _normalize_dev_header_role(value: str) -> Role:
    r = value.strip().lower()
    if r not in VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role. Allowed: {', '.join(sorted(VALID_ROLES))}",
        )
    return r  # type: ignore[return-value]


def get_user_context(
    request: Request,
    db: Session = Depends(get_db),
    x_user: str | None = Header(default=None, alias="X-User"),
    x_role: str | None = Header(default=None, alias="X-Role"),
    x_current_organization_id: str | None = Header(
        default=None,
        alias="X-Current-Organization-Id",
        description="Dev/tests only: mimic session current org when using X-User / X-Role.",
    ),
) -> UserContext:
    raw_cookie = request.cookies.get(settings.session_cookie_name)
    if raw_cookie:
        row = auth_service.get_session_by_raw_token(db, raw_cookie)
        if row is not None:
            user = db.query(User).filter(User.id == row.user_id).first()
            if user is not None:
                if (user.status or "active") != "active":
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="This account has been disabled.",
                    )
                org_id, role = resolve_effective_org_role(db, user, row)
                return UserContext(
                    user_id=user.id,
                    email=user.email,
                    display_name=user.display_name,
                    role=role,
                    current_organization_id=org_id,
                    session=row,
                    legacy_membership_key=None,
                    is_platform_root=user.is_root_admin,
                )

    if settings.allow_dev_header_auth and (x_user or x_role):
        u = (x_user or "anonymous").strip() or "anonymous"
        role = _normalize_dev_header_role(x_role or "viewer")
        dev_org: UUID | None = None
        if x_current_organization_id and x_current_organization_id.strip():
            try:
                dev_org = UUID(x_current_organization_id.strip())
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid X-Current-Organization-Id (expected UUID).",
                ) from exc
        return UserContext(
            user_id=None,
            email=u,
            display_name=u,
            role=role,
            current_organization_id=dev_org,
            session=None,
            legacy_membership_key=u,
            is_platform_root=role == "root_admin",
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated. Sign in or use a valid session cookie.",
    )


def can_approve(role: str) -> bool:
    return role in {"approver", "org_admin", "root_admin"}


def can_execute(role: str) -> bool:
    return role in {"admin", "org_admin", "root_admin"}


def membership_subject_filter(query, ctx: UserContext):
    """Restrict OrgMembership queries to the current principal."""
    if ctx.user_id is not None:
        return query.filter(OrgMembership.user_id == ctx.user_id)
    return query.filter(OrgMembership.user_identifier == ctx.legacy_membership_key)
