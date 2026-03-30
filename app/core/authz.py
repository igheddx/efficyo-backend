"""
Authorization: org-scoped roles from membership, in context of current organization.

Authentication (who the user is) is handled when building `User` + `AuthSession`.
This module resolves *effective organization* and *role* for API authorization and /me.

Rules:
- Role for a selected org comes from `OrgMembership.role` when a row exists.
- Platform operators (`User.is_root_admin`) may act in an organization without a
  membership row; in that case the effective role in that org is `root_admin`.
- If the session references an org the user cannot access, return (None, viewer).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.organization import Organization, OrgMembership
from app.models.user import AuthSession, User

if TYPE_CHECKING:
    pass

Role = Literal["root_admin", "org_admin", "admin", "approver", "viewer", "member"]
VALID_ROLES: frozenset[str] = frozenset(
    {"root_admin", "org_admin", "admin", "approver", "viewer", "member"}
)


def _normalize_membership_role(raw: str) -> Role:
    r = raw.strip().lower()
    if r not in VALID_ROLES:
        return "viewer"
    return r  # type: ignore[return-value]


def resolve_effective_org_role(
    db: Session, user: User, session_row: AuthSession | None
) -> tuple[UUID | None, Role]:
    """Resolve (current_organization_id, role) from session + org memberships."""
    memberships = (
        db.query(OrgMembership).filter(OrgMembership.user_id == user.id).all()
    )
    distinct_orgs = {m.organization_id for m in memberships}

    org_id = session_row.current_organization_id if session_row else None
    if org_id is None and len(distinct_orgs) == 1:
        org_id = next(iter(distinct_orgs))

    if org_id is None:
        return None, "viewer"

    org_row = db.query(Organization).filter(Organization.id == org_id).first()
    if (
        org_row is not None
        and (org_row.status or "active") == "disabled"
        and not user.is_root_admin
    ):
        return None, "viewer"

    m = next((x for x in memberships if x.organization_id == org_id), None)
    if m is not None:
        return org_id, _normalize_membership_role(m.role)

    if user.is_root_admin:
        return org_id, "root_admin"

    # Session org is not accessible to this user
    return None, "viewer"
