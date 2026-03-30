"""
Enforce organization scope for tenant and cloud-account data (multi-org / MSP).

All tenant/cloud inventory and optimization APIs must use the session's current organization
so switching orgs cannot leak data across org boundaries.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.user_context import UserContext
from app.services import access_resolution_service
from app.models.organization import Organization, OrgMembership
from app.models.tenant import Tenant
from app.models.user import User


def require_data_access_organization_id(db: Session, ctx: UserContext) -> UUID:
    """
    Organization ID used to filter tenants and cloud accounts.

    Requires a selected organization (session or dev header). Dev-only: use
    X-Current-Organization-Id together with X-User / X-Role for tests.
    """
    oid = ctx.current_organization_id
    if oid is not None:
        _assert_user_may_access_org(db, ctx, oid)
        return oid

    if ctx.user_id is None:
        if settings.allow_dev_header_auth:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Select an organization (use X-Current-Organization-Id in dev) "
                    "to list tenants and cloud accounts."
                ),
            )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")

    user = db.query(User).filter(User.id == ctx.user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")

    if user.is_root_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Select an organization to load tenant and cloud account data.",
        )

    n = (
        db.query(OrgMembership.id)
        .join(Organization, Organization.id == OrgMembership.organization_id)
        .filter(OrgMembership.user_id == user.id)
        .count()
    )
    if n == 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of any active organization.",
        )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Select an organization to continue.",
    )


def _assert_user_may_access_org(db: Session, ctx: UserContext, organization_id: UUID) -> None:
    org = db.query(Organization).filter(Organization.id == organization_id).first()
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found.")
    if (org.status or "active") == "disabled" and not ctx.is_platform_root:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="That organization is disabled.",
        )

    if ctx.user_id is None:
        # Dev header path: org id supplied explicitly; no DB user to verify membership.
        return

    user = db.query(User).filter(User.id == ctx.user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")
    if user.is_root_admin:
        return

    m = (
        db.query(OrgMembership.id)
        .filter(
            OrgMembership.user_id == user.id,
            OrgMembership.organization_id == organization_id,
        )
        .first()
    )
    if m is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of the selected organization.",
        )


def require_tenant_accessible(db: Session, ctx: UserContext, tenant_id: UUID) -> Tenant:
    """404 if tenant missing or not in the current organization scope."""
    org_id = require_data_access_organization_id(db, ctx)
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found.")

    t_org = tenant.organization_id
    if t_org is None or t_org != org_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found.",
        )
    if not access_resolution_service.tenant_has_any_account_access(db, ctx, tenant_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found.",
        )
    return tenant


def require_tenant_write_role(ctx: UserContext) -> None:
    if ctx.is_platform_root:
        return
    r = (ctx.role or "").strip().lower()
    if r in {"org_admin", "root_admin", "admin"}:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Not allowed to create tenants for this organization.",
    )


def assert_organization_accessible(db: Session, ctx: UserContext, organization_id: UUID) -> None:
    """Raise if the principal cannot access the given organization (404 / 403)."""
    _assert_user_may_access_org(db, ctx, organization_id)
