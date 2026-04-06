from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.authz import resolve_effective_org_role
from app.core.user_context import UserContext
from app.models.cloud_account import CloudAccount
from app.models.organization import Organization, OrgMembership
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.me import (
    CloudAccountSummary,
    ContextDefaultsPatch,
    MeRead,
    MembershipRead,
    OrganizationSummary,
    TenantSummary,
    UserContextDefaultsRead,
)
from app.schemas.user_notification_destination import (
    UserNotificationDestinationPatch,
    UserNotificationDestinationRead,
)
from app.services import access_resolution_service, context_defaults_service, org_service, tenant_service
from app.services import user_notification_destination_service


def _membership_rows(db: Session, user: User) -> list[tuple[OrgMembership, Organization]]:
    return (
        db.query(OrgMembership, Organization)
        .join(Organization, Organization.id == OrgMembership.organization_id)
        .filter(OrgMembership.user_id == user.id)
        .order_by(Organization.name.asc())
        .all()
    )


def _membership_role_for_org(db: Session, user_id: UUID, organization_id: UUID | None) -> str | None:
    """Stored OrgMembership.role for the selected org (admin / approver / viewer / org_admin / …)."""
    if organization_id is None:
        return None
    row = (
        db.query(OrgMembership.role)
        .filter(
            OrgMembership.user_id == user_id,
            OrgMembership.organization_id == organization_id,
        )
        .first()
    )
    if row is None:
        return None
    return (row[0] or "member").strip().lower()


def _accessible_organizations(db: Session, user: User) -> list[OrganizationSummary]:
    """Same visibility as GET /orgs for session-backed users (root: all; else membership orgs)."""
    if user.is_root_admin:
        rows = db.query(Organization).order_by(Organization.name.asc()).all()
        return [OrganizationSummary(id=o.id, name=o.name) for o in rows]
    seen: set[UUID] = set()
    out: list[OrganizationSummary] = []
    for _m, org in _membership_rows(db, user):
        if (org.status or "active") != "active":
            continue
        if org.id in seen:
            continue
        seen.add(org.id)
        out.append(OrganizationSummary(id=org.id, name=org.name))
    return out


def build_me_read(
    db: Session,
    ctx: UserContext,
    *,
    selection_tenant_id: UUID | None = None,
    selection_cloud_account_id: UUID | None = None,
) -> MeRead:
    # Separated dev/test path: no DB user row; not used by the normal SPA login flow.
    if ctx.user_id is None:
        org_l = access_resolution_service.org_membership_role_label(ctx.role)
        eff = None
        if selection_tenant_id is not None and selection_cloud_account_id is not None:
            eff = access_resolution_service.resolve_effective_access(
                db, ctx, selection_tenant_id, selection_cloud_account_id
            )
        dev_can_manage = False
        if ctx.current_organization_id is not None:
            dev_can_manage = org_service.can_manage_org(db, ctx, ctx.current_organization_id)
        return MeRead(
            id=None,
            email=ctx.email,
            display_name=ctx.display_name,
            auth_provider="dev_header",
            external_subject_id=None,
            memberships=[],
            accessible_organizations=[],
            current_organization=None,
            current_role=ctx.role,
            current_org_role=org_l if org_l in ("org_admin", "member", "root_admin") else ctx.role,
            effective_access_role=eff,
            accessible_tenants=[],
            accessible_cloud_accounts=[],
            is_root_admin=ctx.is_platform_root,
            can_manage_platform_orgs=ctx.is_platform_root,
            can_manage_current_organization=dev_can_manage,
            context_defaults=UserContextDefaultsRead(),
        )

    user = db.query(User).filter(User.id == ctx.user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")

    ctx_work = ctx
    if ctx.session is not None:
        ctx_work = context_defaults_service.ensure_session_organization_valid(db, user, ctx)
        context_defaults_service.reconcile_user_context_defaults(db, user, ctx_work)
        db.refresh(user)

    rows = _membership_rows(db, user)
    memberships = [
        MembershipRead(
            organization_id=org.id,
            organization_name=org.name,
            role=m.role,
        )
        for m, org in rows
        if (org.status or "active") == "active"
    ]

    current_org = None
    if ctx_work.current_organization_id is not None:
        o = db.query(Organization).filter(Organization.id == ctx_work.current_organization_id).first()
        if o is not None:
            current_org = OrganizationSummary(id=o.id, name=o.name)

    mship = _membership_role_for_org(db, user.id, ctx_work.current_organization_id)
    if mship is not None:
        org_role_label = mship
    else:
        org_role_label = access_resolution_service.org_membership_role_label(ctx_work.role)
        if org_role_label not in ("org_admin", "member", "root_admin"):
            org_role_label = "member"

    accessible_tenants: list[TenantSummary] = []
    accessible_cloud_accounts: list[CloudAccountSummary] = []
    if ctx_work.current_organization_id is not None:
        raw_tenants = tenant_service.list_tenants_for_organization(
            db, ctx_work.current_organization_id, skip=0, limit=200
        )
        for t in raw_tenants:
            if access_resolution_service.tenant_has_any_account_access(db, ctx_work, t.id):
                accessible_tenants.append(TenantSummary(id=t.id, name=t.name))

    eff_access = None
    if selection_tenant_id is not None and selection_cloud_account_id is not None:
        eff_access = access_resolution_service.resolve_effective_access(
            db, ctx_work, selection_tenant_id, selection_cloud_account_id
        )
        if ctx_work.current_organization_id is not None:
            cas = (
                db.query(CloudAccount)
                .filter(CloudAccount.tenant_id == selection_tenant_id)
                .order_by(CloudAccount.name.asc())
                .all()
            )
            visible = access_resolution_service.filter_cloud_accounts_visible(
                db, ctx_work, selection_tenant_id, [c.id for c in cas]
            )
            vis_set = set(visible)
            for c in cas:
                if c.id in vis_set:
                    accessible_cloud_accounts.append(
                        CloudAccountSummary(id=c.id, name=c.name, account_id=c.account_id)
                    )

    can_manage_current = False
    if ctx_work.current_organization_id is not None:
        can_manage_current = org_service.can_manage_org(db, ctx_work, ctx_work.current_organization_id)

    return MeRead(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        auth_provider=(user.auth_provider or "local").strip().lower() or "local",
        external_subject_id=user.external_subject_id,
        memberships=memberships,
        accessible_organizations=_accessible_organizations(db, user),
        current_organization=current_org,
        current_role=ctx_work.role,
        current_org_role=org_role_label,
        effective_access_role=eff_access,
        accessible_tenants=accessible_tenants,
        accessible_cloud_accounts=accessible_cloud_accounts,
        is_root_admin=bool(user.is_root_admin),
        can_manage_platform_orgs=bool(user.is_root_admin),
        can_manage_current_organization=can_manage_current,
        receive_approval_emails=bool(getattr(user, 'receive_approval_emails', False)),
        context_defaults=UserContextDefaultsRead(
            organization_id=user.default_organization_id,
            tenant_id=user.default_tenant_id,
            cloud_account_id=user.default_cloud_account_id,
        ),
    )


def set_current_organization(db: Session, ctx: UserContext, organization_id: UUID) -> MeRead:
    if ctx.session is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A browser session is required to set the current organization. Sign in again.",
        )
    if ctx.user_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid session context.")

    user = db.query(User).filter(User.id == ctx.user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")

    if user.is_root_admin:
        org = db.query(Organization).filter(Organization.id == organization_id).first()
        if org is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found.")
    else:
        m = (
            db.query(OrgMembership)
            .filter(
                OrgMembership.user_id == user.id,
                OrgMembership.organization_id == organization_id,
            )
            .first()
        )
        if m is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not a member of that organization.",
            )
        org_check = db.query(Organization).filter(Organization.id == organization_id).first()
        if org_check is not None and (org_check.status or "active") == "disabled":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="That organization is disabled.",
            )

    ctx.session.current_organization_id = organization_id
    db.commit()
    db.refresh(ctx.session)

    oid, role = resolve_effective_org_role(db, user, ctx.session)
    next_ctx = UserContext(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=role,
        current_organization_id=oid,
        session=ctx.session,
        legacy_membership_key=None,
        is_platform_root=user.is_root_admin,
    )
    return build_me_read(db, next_ctx)


def list_context_default_tenants(
    db: Session,
    ctx: UserContext,
    organization_id: UUID,
) -> list[TenantSummary]:
    """Grant-scoped tenants in an org for default-context UI (org role resolved for that org)."""
    if ctx.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session required to list default-context tenants.",
        )
    user = db.query(User).filter(User.id == ctx.user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")
    rows = context_defaults_service.list_tenants_for_default_context_editor(
        db,
        user=user,
        base_ctx=ctx,
        organization_id=organization_id,
    )
    return [TenantSummary(id=t.id, name=t.name) for t in rows]


def list_context_default_cloud_accounts(
    db: Session,
    ctx: UserContext,
    tenant_id: UUID,
) -> list[CloudAccountSummary]:
    """Grant-filtered cloud accounts for default-context UI (any accessible org)."""
    if ctx.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session required to list default-context cloud accounts.",
        )
    user = db.query(User).filter(User.id == ctx.user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")
    rows = context_defaults_service.list_cloud_accounts_for_default_context_editor(
        db,
        user=user,
        base_ctx=ctx,
        tenant_id=tenant_id,
    )
    return [
        CloudAccountSummary(id=c.id, name=c.name, account_id=c.account_id or None) for c in rows
    ]


def patch_user_preferences(db: Session, ctx: UserContext, *, receive_approval_emails: bool) -> MeRead:
    if ctx.user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    user = db.query(User).filter(User.id == ctx.user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")
    user.receive_approval_emails = receive_approval_emails
    db.add(user)
    db.commit()
    db.refresh(user)
    return build_me_read(db, ctx)


def patch_user_context_defaults(db: Session, ctx: UserContext, body: ContextDefaultsPatch) -> MeRead:
    if ctx.session is None or ctx.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session required to update context defaults.",
        )
    user = db.query(User).filter(User.id == ctx.user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")
    updates = body.model_dump(exclude_unset=True)
    if updates:
        context_defaults_service.apply_context_defaults_patch(db, user, ctx, updates)
        db.refresh(user)
    ctx_work = context_defaults_service.ensure_session_organization_valid(db, user, ctx)
    context_defaults_service.reconcile_user_context_defaults(db, user, ctx_work)
    db.refresh(user)
    return build_me_read(db, ctx_work)


def get_my_notification_destination(
    db: Session,
    ctx: UserContext,
    *,
    organization_id: UUID,
) -> UserNotificationDestinationRead:
    user = None
    if ctx.user_id is not None:
        user = db.query(User).filter(User.id == ctx.user_id).first()
    elif ctx.email:
        user = db.query(User).filter(User.email == str(ctx.email).strip()).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    if not user_notification_destination_service.can_user_access_org(
        db, user.id, organization_id, bool(user.is_root_admin)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization not accessible.")

    row = user_notification_destination_service.get_or_create_destination(
        db,
        user_id=user.id,
        organization_id=organization_id,
    )
    return UserNotificationDestinationRead.model_validate(row)


def patch_my_notification_destination(
    db: Session,
    ctx: UserContext,
    body: UserNotificationDestinationPatch,
) -> UserNotificationDestinationRead:
    user = None
    if ctx.user_id is not None:
        user = db.query(User).filter(User.id == ctx.user_id).first()
    elif ctx.email:
        user = db.query(User).filter(User.email == str(ctx.email).strip()).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    if not user_notification_destination_service.can_user_access_org(
        db, user.id, body.organization_id, bool(user.is_root_admin)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization not accessible.")

    updates = body.model_dump(exclude_unset=True)
    updates.pop("organization_id", None)
    row = user_notification_destination_service.patch_destination(
        db,
        user_id=user.id,
        organization_id=body.organization_id,
        updates=updates,
    )
    return UserNotificationDestinationRead.model_validate(row)
