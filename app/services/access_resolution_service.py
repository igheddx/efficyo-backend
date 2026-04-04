"""
Resolve tenant/cloud operational access from org membership + access_grants.

Org membership role (org_admin | member) is separate; this module answers
viewer / approver / admin / none for the selected customer tenant and AWS account.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.access_grant import AccessGrant
from app.models.cloud_account import CloudAccount
from app.models.tenant import Tenant

AccessLevel = Literal["none", "viewer", "approver", "admin"]


def access_rank(level: str) -> int:
    return {"none": 0, "viewer": 1, "approver": 2, "admin": 3}.get((level or "none").lower(), 0)


def max_access_level(a: str, b: str) -> str:
    levels = ("none", "viewer", "approver", "admin")
    return levels[max(access_rank(a), access_rank(b))]


def org_membership_role_label(raw: str) -> str:
    """Normalize stored membership role for API/UI (org administration)."""
    r = (raw or "").strip().lower()
    if r in ("org_admin", "member", "root_admin"):
        return r
    if r in ("admin", "approver", "viewer"):
        return "member"
    return r


def _membership_implies_org_wide_admin(membership_role: str) -> bool:
    """
    Full operational admin on every tenant/account in the org.

    Includes legacy MSP ``admin`` membership (see ``org_service.can_manage_org``); same idea as ``org_admin``
    for access resolution, while grant-based ``member`` rows still use ``access_grants`` only.
    """
    return (membership_role or "").strip().lower() in ("org_admin", "root_admin", "admin")


def _legacy_flat_operational_role(membership_role: str) -> AccessLevel | None:
    """
    Pre-grant model: membership carried operational roles for the whole org.
    Returns None when the role does not imply implicit tenant-wide ops (e.g. member).
    """
    r = (membership_role or "").strip().lower()
    if r in ("org_admin", "root_admin", "admin"):
        return "admin"
    if r == "approver":
        return "approver"
    if r == "viewer":
        return "viewer"
    return None


def _dev_header_effective_access(ctx: UserContext) -> AccessLevel:
    """X-User / X-Role tests: no DB user or grants; mirror legacy flat role semantics."""
    leg = _legacy_flat_operational_role(ctx.role)
    if leg is not None:
        return leg
    return "none"


def _has_any_grant_in_org(db: Session, *, user_id: UUID, organization_id: UUID) -> bool:
    return (
        db.query(AccessGrant.id)
        .filter(
            AccessGrant.user_id == user_id,
            AccessGrant.organization_id == organization_id,
        )
        .first()
        is not None
    )


def user_has_admin_access_grant_in_org(db: Session, ctx: UserContext, organization_id: UUID) -> bool:
    """
    After migration 025, former membership `admin` becomes `member` plus tenant-wide `admin` grants.
    Org management (users, tenants, cloud accounts) should still apply for those principals.
    """
    if ctx.user_id is None:
        return False
    return (
        db.query(AccessGrant.id)
        .filter(
            AccessGrant.user_id == ctx.user_id,
            AccessGrant.organization_id == organization_id,
            AccessGrant.access_role == "admin",
        )
        .first()
        is not None
    )


def _grants_for_tenant(
    db: Session,
    *,
    user_id: UUID,
    organization_id: UUID,
    tenant_id: UUID,
) -> list[AccessGrant]:
    return (
        db.query(AccessGrant)
        .filter(
            AccessGrant.user_id == user_id,
            AccessGrant.organization_id == organization_id,
            AccessGrant.tenant_id == tenant_id,
        )
        .all()
    )


def _best_role_from_grant_rows(rows: list[AccessGrant], *, cloud_account_id: UUID) -> AccessLevel:
    tenant_wide = [g for g in rows if g.cloud_account_id is None]
    acct = [g for g in rows if g.cloud_account_id == cloud_account_id]
    chosen: list[AccessGrant] = acct if acct else tenant_wide
    if not chosen:
        return "none"
    best = "none"
    for g in chosen:
        ar = (g.access_role or "").strip().lower()
        if ar not in ("viewer", "approver", "admin"):
            continue
        best = max_access_level(best, ar)
    return best  # type: ignore[return-value]


def resolve_effective_access(
    db: Session,
    ctx: UserContext,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> AccessLevel:
    """
    Effective operational role for the current principal in (tenant, cloud account).

    v1 rules:
    - Platform root: admin everywhere.
    - Org membership org_admin, root_admin, or legacy admin: admin on all tenants/accounts in that org.
    - If the user has at least one access grant row in this org: use grants for this tenant;
      account-specific grant overrides tenant-wide grant for that account.
    - If there are no grant rows for this user in the org: fall back to legacy membership
      operational mapping (unmigrated DBs and dev headers).
    """
    if ctx.is_platform_root:
        return "admin"

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant is None or tenant.organization_id is None:
        return "none"
    org_id = tenant.organization_id

    if ctx.user_id is None:
        return _dev_header_effective_access(ctx)

    if _membership_implies_org_wide_admin(ctx.role):
        return "admin"

    if not _has_any_grant_in_org(db, user_id=ctx.user_id, organization_id=org_id):
        leg = _legacy_flat_operational_role(ctx.role)
        return leg if leg is not None else "none"

    rows = _grants_for_tenant(db, user_id=ctx.user_id, organization_id=org_id, tenant_id=tenant_id)
    return _best_role_from_grant_rows(rows, cloud_account_id=cloud_account_id)


def resolve_effective_access_for_user(
    db: Session,
    *,
    user_id: UUID,
    organization_id: UUID,
    tenant_id: UUID,
    cloud_account_id: UUID,
    membership_role: str,
    is_platform_root: bool = False,
) -> AccessLevel:
    """Assignee eligibility (multi-approver) without a full UserContext."""
    if is_platform_root:
        return "admin"
    if _membership_implies_org_wide_admin(membership_role):
        return "admin"
    if not _has_any_grant_in_org(db, user_id=user_id, organization_id=organization_id):
        leg = _legacy_flat_operational_role(membership_role)
        return leg if leg is not None else "none"
    rows = _grants_for_tenant(db, user_id=user_id, organization_id=organization_id, tenant_id=tenant_id)
    return _best_role_from_grant_rows(rows, cloud_account_id=cloud_account_id)


def tenant_has_any_account_access(db: Session, ctx: UserContext, tenant_id: UUID) -> bool:
    """True if the user may open this customer tenant (at least one visible cloud account or tenant-wide grant)."""
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant is None or tenant.organization_id is None:
        return False
    org_id = tenant.organization_id
    if ctx.is_platform_root:
        return True
    if ctx.user_id is None:
        return _dev_header_effective_access(ctx) != "none"

    if _membership_implies_org_wide_admin(ctx.role):
        return True

    if not _has_any_grant_in_org(db, user_id=ctx.user_id, organization_id=org_id):
        return _legacy_flat_operational_role(ctx.role) is not None

    rows = _grants_for_tenant(db, user_id=ctx.user_id, organization_id=org_id, tenant_id=tenant_id)
    if not rows:
        return False
    if any(g.cloud_account_id is None for g in rows):
        return True
    allowed = {g.cloud_account_id for g in rows if g.cloud_account_id is not None}
    accounts = db.query(CloudAccount.id).filter(CloudAccount.tenant_id == tenant_id).all()
    for (aid,) in accounts:
        if aid in allowed:
            return True
    return False


def filter_cloud_accounts_visible(
    db: Session,
    ctx: UserContext,
    tenant_id: UUID,
    cloud_account_ids: list[UUID],
) -> list[UUID]:
    out: list[UUID] = []
    for cid in cloud_account_ids:
        if resolve_effective_access(db, ctx, tenant_id, cid) != "none":
            out.append(cid)
    return out


def require_min_effective_access(
    db: Session,
    ctx: UserContext,
    tenant_id: UUID,
    cloud_account_id: UUID,
    *,
    minimum: AccessLevel,
) -> AccessLevel:
    eff = resolve_effective_access(db, ctx, tenant_id, cloud_account_id)
    if access_rank(eff) < access_rank(minimum):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient access for this tenant or cloud account.",
        )
    return eff


def user_may_list_org_approval_requests(db: Session, ctx: UserContext, org_id: UUID) -> bool:
    """Org-wide approval queue / approval-requests list."""
    if ctx.is_platform_root:
        return True
    if ctx.user_id is None:
        return access_rank(_dev_header_effective_access(ctx)) >= access_rank("approver")
    label = org_membership_role_label(ctx.role)
    if label in ("org_admin", "root_admin"):
        return True
    if not _has_any_grant_in_org(db, user_id=ctx.user_id, organization_id=org_id):
        leg = _legacy_flat_operational_role(ctx.role)
        return leg in ("approver", "admin")
    q = (
        db.query(AccessGrant.id)
        .filter(
            AccessGrant.user_id == ctx.user_id,
            AccessGrant.organization_id == org_id,
            AccessGrant.access_role.in_(("approver", "admin")),
        )
        .first()
    )
    return q is not None


def user_may_admin_view_approval_requests(db: Session, ctx: UserContext, org_id: UUID) -> bool:
    if ctx.is_platform_root:
        return True
    if ctx.user_id is None:
        return access_rank(_dev_header_effective_access(ctx)) >= access_rank("admin")
    label = org_membership_role_label(ctx.role)
    if label in ("org_admin", "root_admin"):
        return True
    if not _has_any_grant_in_org(db, user_id=ctx.user_id, organization_id=org_id):
        return _legacy_flat_operational_role(ctx.role) == "admin"
    q = (
        db.query(AccessGrant.id)
        .filter(
            AccessGrant.user_id == ctx.user_id,
            AccessGrant.organization_id == org_id,
            AccessGrant.access_role == "admin",
        )
        .first()
    )
    return q is not None


def can_submit_multi_approval_for_context(
    db: Session,
    ctx: UserContext,
    *,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> bool:
    """
    Who may start a multi-step approval (picker + POST /approval-requests).

    Same floor as acting on an assignment: approver or admin on this tenant/account, plus org-wide admins.
    """
    if ctx.is_platform_root:
        return True
    if _membership_implies_org_wide_admin(ctx.role):
        return True
    eff = resolve_effective_access(db, ctx, tenant_id, cloud_account_id)
    return access_rank(eff) >= access_rank("admin")


def user_can_approve_assignment(
    db: Session,
    ctx: UserContext,
    *,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> bool:
    return access_rank(resolve_effective_access(db, ctx, tenant_id, cloud_account_id)) >= access_rank("approver")
