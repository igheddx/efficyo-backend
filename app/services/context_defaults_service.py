"""
Persisted default org / tenant / cloud account and session alignment for MSP context switching.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.authz import resolve_effective_org_role
from app.models.cloud_account import CloudAccount
from app.models.organization import Organization, OrgMembership
from app.models.tenant import Tenant
from app.models.user import User
from app.services import access_resolution_service, tenant_service


def _accessible_org_ids_ordered(db: Session, user: User) -> list[UUID]:
    if user.is_root_admin:
        rows = (
            db.query(Organization.id)
            .filter(Organization.status == "active")
            .order_by(Organization.name.asc())
            .all()
        )
        return [r[0] for r in rows]
    out: list[UUID] = []
    seen: set[UUID] = set()
    q = (
        db.query(Organization.id)
        .join(OrgMembership, OrgMembership.organization_id == Organization.id)
        .filter(OrgMembership.user_id == user.id)
        .filter(Organization.status == "active")
        .order_by(Organization.name.asc())
    )
    for (oid,) in q:
        if oid not in seen:
            seen.add(oid)
            out.append(oid)
    return out


def _accessible_org_id_set(db: Session, user: User) -> set[UUID]:
    return set(_accessible_org_ids_ordered(db, user))


def pick_initial_session_organization_id(db: Session, user: User) -> UUID | None:
    """Prefer saved default org when valid; else single membership org."""
    acc = _accessible_org_ids_ordered(db, user)
    if not acc:
        return None
    d = user.default_organization_id
    if d is not None and d in acc:
        return d
    if len(acc) == 1:
        return acc[0]
    return None


def _build_ctx_from_session(db: Session, user: User, session):
    from app.core.user_context import UserContext

    oid, role = resolve_effective_org_role(db, user, session)
    return UserContext(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=role,
        current_organization_id=oid,
        session=session,
        legacy_membership_key=None,
        is_platform_root=bool(user.is_root_admin),
    )


def ensure_session_organization_valid(db: Session, user: User, ctx: UserContext) -> UserContext:
    """
    If the session points at no org or an org the user cannot access, pick a valid org
    (defaults first, then first accessible) and persist on the session.
    """
    if ctx.user_id is None or ctx.session is None:
        return ctx
    acc = _accessible_org_ids_ordered(db, user)
    if not acc:
        if ctx.session.current_organization_id is not None:
            ctx.session.current_organization_id = None
            db.add(ctx.session)
            db.commit()
            db.refresh(ctx.session)
        return _build_ctx_from_session(db, user, ctx.session)

    cur = ctx.session.current_organization_id
    chosen: UUID | None = None
    if cur is not None and cur in acc:
        chosen = cur
    elif user.default_organization_id is not None and user.default_organization_id in acc:
        chosen = user.default_organization_id
    else:
        chosen = acc[0]

    if chosen != cur:
        ctx.session.current_organization_id = chosen
        db.add(ctx.session)
        db.commit()
        db.refresh(ctx.session)
    return _build_ctx_from_session(db, user, ctx.session)


def _accessible_tenant_ids_for_ctx(db: Session, ctx: UserContext, organization_id: UUID) -> list[UUID]:
    raw = tenant_service.list_tenants_for_organization(db, organization_id, skip=0, limit=500)
    out: list[UUID] = []
    for t in raw:
        if access_resolution_service.tenant_has_any_account_access(db, ctx, t.id):
            out.append(t.id)
    return out


def list_tenants_for_default_context_editor(
    db: Session,
    *,
    user: User,
    base_ctx: UserContext,
    organization_id: UUID,
) -> list[Tenant]:
    """
    Tenants in `organization_id` the user may use when editing saved defaults.

    Resolves org-scoped membership role for `organization_id` (not the session's current org)
    so grant checks match the org being configured.
    """
    if organization_id not in _accessible_org_id_set(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of that organization.",
        )
    vctx = _ctx_for_organization(db, user, base_ctx, organization_id)
    raw = tenant_service.list_tenants_for_organization(db, organization_id, skip=0, limit=500)
    return [t for t in raw if access_resolution_service.tenant_has_any_account_access(db, vctx, t.id)]


def list_cloud_accounts_for_default_context_editor(
    db: Session,
    *,
    user: User,
    base_ctx: UserContext,
    tenant_id: UUID,
) -> list[CloudAccount]:
    """
    Visible cloud accounts for a tenant when editing saved defaults.

    Uses the tenant's organization (not the session's current org) so users can configure
    defaults for another accessible MSP org without switching the working session first.
    """
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant is None or tenant.organization_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found.")
    org_id = tenant.organization_id
    if org_id not in _accessible_org_id_set(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of that organization.",
        )
    vctx = _ctx_for_organization(db, user, base_ctx, org_id)
    if not access_resolution_service.tenant_has_any_account_access(db, vctx, tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found.")
    rows = (
        db.query(CloudAccount)
        .filter(CloudAccount.tenant_id == tenant_id)
        .order_by(CloudAccount.name.asc())
        .all()
    )
    ids = [c.id for c in rows]
    allowed = set(access_resolution_service.filter_cloud_accounts_visible(db, vctx, tenant_id, ids))
    return [c for c in rows if c.id in allowed]


def _visible_cloud_ids_for_tenant(db: Session, ctx: UserContext, tenant_id: UUID) -> list[UUID]:
    cas = (
        db.query(CloudAccount.id)
        .filter(CloudAccount.tenant_id == tenant_id)
        .order_by(CloudAccount.name.asc())
        .all()
    )
    ids = [c[0] for c in cas]
    return access_resolution_service.filter_cloud_accounts_visible(db, ctx, tenant_id, ids)


def reconcile_user_context_defaults(db: Session, user: User, ctx: UserContext) -> None:
    """
    Clear invalid defaults; fill **null** defaults with first valid org/tenant/cloud.
    Does not overwrite a valid saved default_organization_id when the session org differs (UI switch).
    """
    if ctx.user_id is None:
        return
    acc_ordered = _accessible_org_ids_ordered(db, user)
    acc_orgs = set(acc_ordered)
    if not acc_orgs:
        if (
            user.default_organization_id is not None
            or user.default_tenant_id is not None
            or user.default_cloud_account_id is not None
        ):
            user.default_organization_id = None
            user.default_tenant_id = None
            user.default_cloud_account_id = None
            db.add(user)
            db.commit()
            db.refresh(user)
        return

    changed = False
    if user.default_organization_id is not None and user.default_organization_id not in acc_orgs:
        user.default_organization_id = None
        user.default_tenant_id = None
        user.default_cloud_account_id = None
        changed = True

    if user.default_organization_id is None:
        fallback_org = ctx.current_organization_id if ctx.current_organization_id in acc_orgs else acc_ordered[0]
        user.default_organization_id = fallback_org
        user.default_tenant_id = None
        user.default_cloud_account_id = None
        changed = True

    org_id = user.default_organization_id
    if org_id is None or org_id not in acc_orgs:
        if changed:
            db.add(user)
            db.commit()
            db.refresh(user)
        return

    vctx = _ctx_for_organization(db, user, ctx, org_id)
    tenant_ids = _accessible_tenant_ids_for_ctx(db, vctx, org_id)
    if not tenant_ids:
        if user.default_tenant_id is not None or user.default_cloud_account_id is not None:
            user.default_tenant_id = None
            user.default_cloud_account_id = None
            changed = True
        if changed:
            db.add(user)
            db.commit()
            db.refresh(user)
        return

    if user.default_tenant_id is None or user.default_tenant_id not in tenant_ids:
        user.default_tenant_id = tenant_ids[0]
        user.default_cloud_account_id = None
        changed = True

    t = db.query(Tenant).filter(Tenant.id == user.default_tenant_id).first()
    if t is None or t.organization_id != org_id:
        user.default_tenant_id = tenant_ids[0]
        user.default_cloud_account_id = None
        changed = True

    cloud_ids = _visible_cloud_ids_for_tenant(db, vctx, user.default_tenant_id)
    if not cloud_ids:
        if user.default_cloud_account_id is not None:
            user.default_cloud_account_id = None
            changed = True
    elif user.default_cloud_account_id is None or user.default_cloud_account_id not in cloud_ids:
        user.default_cloud_account_id = cloud_ids[0]
        changed = True

    if changed:
        db.add(user)
        db.commit()
        db.refresh(user)


def maybe_seed_defaults_on_first_grant(
    db: Session,
    *,
    user_id: UUID,
    organization_id: UUID,
    tenant_id: UUID,
    cloud_account_id: UUID | None,
) -> None:
    """When a user receives their first tenant/account grant, set defaults if unset."""
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        return
    if user.default_organization_id is not None:
        return
    user.default_organization_id = organization_id
    user.default_tenant_id = tenant_id
    user.default_cloud_account_id = cloud_account_id
    db.add(user)
    db.commit()
    db.refresh(user)


class _SessionOrgShim:
    __slots__ = ("current_organization_id",)

    def __init__(self, organization_id: UUID | None) -> None:
        self.current_organization_id = organization_id


def _ctx_for_organization(db: Session, user: User, base, organization_id: UUID):
    """Resolve org role for an arbitrary accessible org without mutating the real session row."""
    from app.core.user_context import UserContext

    oid, role = resolve_effective_org_role(db, user, _SessionOrgShim(organization_id))
    return UserContext(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=role,
        current_organization_id=oid,
        session=base.session,
        legacy_membership_key=None,
        is_platform_root=bool(user.is_root_admin),
    )


def apply_context_defaults_patch(db: Session, user: User, ctx: UserContext, updates: dict) -> None:
    """
    Apply only keys present in `updates` (from model_dump(exclude_unset=True)).
    JSON null clears a field. Validates each non-null value against access in the target org.
    """
    acc_orgs = _accessible_org_id_set(db, user)

    if "default_organization_id" in updates:
        oid = updates["default_organization_id"]
        if oid is not None and oid not in acc_orgs:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="default_organization_id is not an organization you can access.",
            )
        user.default_organization_id = oid
        if oid is None:
            user.default_tenant_id = None
            user.default_cloud_account_id = None

    anchor_org = user.default_organization_id
    if anchor_org is not None and anchor_org not in acc_orgs:
        anchor_org = None

    if "default_tenant_id" in updates:
        tid = updates["default_tenant_id"]
        if tid is None:
            user.default_tenant_id = None
            user.default_cloud_account_id = None
        else:
            if anchor_org is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Set default_organization_id before default_tenant_id.",
                )
            t = db.query(Tenant).filter(Tenant.id == tid).first()
            if t is None or t.organization_id != anchor_org:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="default_tenant_id is not in the selected default organization.",
                )
            vctx = _ctx_for_organization(db, user, ctx, anchor_org)
            if tid not in _accessible_tenant_ids_for_ctx(db, vctx, anchor_org):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="You do not have access to that tenant.",
                )
            user.default_tenant_id = tid
            if user.default_cloud_account_id is not None:
                vis = set(_visible_cloud_ids_for_tenant(db, vctx, tid))
                if user.default_cloud_account_id not in vis:
                    user.default_cloud_account_id = None

    if "default_cloud_account_id" in updates:
        cid = updates["default_cloud_account_id"]
        if cid is None:
            user.default_cloud_account_id = None
        else:
            if user.default_tenant_id is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Set default_tenant_id before default_cloud_account_id.",
                )
            if anchor_org is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Set default_organization_id before default_cloud_account_id.",
                )
            vctx = _ctx_for_organization(db, user, ctx, anchor_org)
            vis = set(_visible_cloud_ids_for_tenant(db, vctx, user.default_tenant_id))
            if cid not in vis:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="You do not have access to that cloud account in the selected tenant.",
                )
            user.default_cloud_account_id = cid

    db.add(user)
    db.commit()
    db.refresh(user)
