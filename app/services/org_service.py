from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.core.constants import DEMO_ORG_NAME
from app.core.org_slug import ensure_unique_org_slug, slugify_name
from app.core.user_context import UserContext, VALID_ROLES, membership_subject_filter
from app.models.organization import OrgMembership, Organization
from app.services import access_resolution_service, auth_service, invite_email_service, tenant_scope_service
from app.services.access_resolution_service import org_membership_role_label


def ensure_demo_org_membership(db_session: Session) -> None:
    """Backward-compatible name: seeds demo users + Demo org (session-era replacement)."""
    auth_service.ensure_local_seed_users(db_session)


def _is_root(ctx: UserContext) -> bool:
    """Platform operator (break-glass), not the same as membership role root_admin in an org."""
    return ctx.is_platform_root


def _membership_for_user_ref(db: Session, org_id: UUID, user_ref: str) -> OrgMembership | None:
    ref = user_ref.strip()
    try:
        uid = UUID(ref)
        return (
            db.query(OrgMembership)
            .filter(OrgMembership.organization_id == org_id, OrgMembership.user_id == uid)
            .first()
        )
    except ValueError:
        return (
            db.query(OrgMembership)
            .filter(OrgMembership.organization_id == org_id, OrgMembership.user_identifier == ref)
            .first()
        )


def _has_org_membership_roles(
    db: Session, ctx: UserContext, org_id: UUID, roles: tuple[str, ...]
) -> bool:
    q = db.query(OrgMembership).filter(
        OrgMembership.organization_id == org_id,
        OrgMembership.role.in_(roles),
    )
    q = membership_subject_filter(q, ctx)
    return q.first() is not None


def can_manage_org(db: Session, ctx: UserContext, org_id: UUID) -> bool:
    if _is_root(ctx):
        return True
    if org_membership_role_label(ctx.role) == "org_admin":
        return _has_org_membership_roles(db, ctx, org_id, ("org_admin",))
    if (ctx.role or "").strip().lower() == "root_admin":
        return _has_org_membership_roles(db, ctx, org_id, ("root_admin",))
    # Legacy MSP operator row (membership role=admin), verified against DB — not org_admin UI.
    if (ctx.role or "").strip().lower() == "admin":
        return _has_org_membership_roles(db, ctx, org_id, ("admin",))
    # Post–025: membership is `member` but former admin/operator is represented by admin access_grants.
    if access_resolution_service.user_has_admin_access_grant_in_org(db, ctx, org_id):
        return True
    return False


def require_org_manager(db: Session, ctx: UserContext, org_id: UUID) -> None:
    if can_manage_org(db, ctx, org_id):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Not allowed to manage this organization.",
    )


def require_list_orgs(ctx: UserContext) -> None:
    if ctx.is_platform_root:
        return
    if ctx.user_id is not None:
        return
    if ctx.legacy_membership_key is not None:
        if (ctx.role or "").strip().lower() == "viewer":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not allowed to list organizations.",
            )
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Not allowed to list organizations.",
    )


def require_create_org(ctx: UserContext) -> None:
    if _is_root(ctx):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Only platform administrators can create organizations.",
    )


def _validate_role_value(role: str, *, actor: UserContext, db: Session, org_id: UUID) -> str:
    r = role.strip().lower()
    if r not in VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role. Allowed: {', '.join(sorted(VALID_ROLES))}",
        )
    if r == "root_admin" and not _is_root(actor):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only platform administrators can assign the root_admin role.",
        )
    if r == "org_admin" and not _is_root(actor):
        cr = (actor.role or "").strip().lower()
        if cr == "org_admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only platform administrators can assign the org_admin role.",
            )
    if not _is_root(actor):
        cr = (actor.role or "").strip().lower()
        manages = can_manage_org(db, actor, org_id)
        if cr == "org_admin":
            if r not in {"admin", "approver", "viewer", "member"}:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Organization admins can only assign member or legacy admin, approver, or viewer.",
                )
        elif cr == "admin" or (cr == "member" and manages):
            if r not in {"admin", "approver", "viewer", "member"}:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not allowed to assign that role.",
                )
        elif cr == "root_admin":
            if r not in {"admin", "approver", "viewer", "member", "org_admin"}:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not allowed to assign that role.",
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not allowed to assign organization roles.",
            )
    return r


def list_organizations(db: Session, ctx: UserContext) -> list[Organization]:
    require_list_orgs(ctx)
    ensure_demo_org_membership(db)
    if _is_root(ctx):
        return db.query(Organization).order_by(Organization.name.asc()).all()
    q = db.query(OrgMembership)
    q = membership_subject_filter(q, ctx)
    org_ids = list({row.organization_id for row in q.all()})
    if not org_ids:
        return []
    return (
        db.query(Organization)
        .filter(Organization.id.in_(org_ids), Organization.status == "active")
        .order_by(Organization.name.asc())
        .all()
    )


def get_organization(db: Session, org_id: UUID, ctx: UserContext) -> Organization:
    require_list_orgs(ctx)
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found.")
    require_org_manager(db, ctx, org_id)
    if (org.status or "active") == "disabled" and not _is_root(ctx):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization is disabled.",
        )
    return org


def organization_member_count(db: Session, org_id: UUID) -> int:
    return (
        db.query(func.count(OrgMembership.id))
        .filter(OrgMembership.organization_id == org_id)
        .scalar()
        or 0
    )


def create_organization(db: Session, name: str, ctx: UserContext) -> Organization:
    require_create_org(ctx)
    trimmed = name.strip()
    if not trimmed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name is required.")
    slug = ensure_unique_org_slug(db, slugify_name(trimmed))
    org = Organization(name=trimmed, slug=slug, status="active")
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def list_members(db: Session, org_id: UUID, ctx: UserContext) -> list[OrgMembership]:
    """
    Org managers always see the roster. Grant-based operational admins (MSP model) need the same
    list to pick approvers for multi-approval — align with access_resolution.user_may_admin_view_approval_requests.
    """
    require_list_orgs(ctx)
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found.")
    tenant_scope_service.assert_organization_accessible(db, ctx, org_id)
    if (org.status or "active") == "disabled" and not _is_root(ctx):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization is disabled.",
        )
    if not can_manage_org(db, ctx, org_id) and not access_resolution_service.user_may_admin_view_approval_requests(
        db, ctx, org_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not allowed to list members of this organization.",
        )
    return (
        db.query(OrgMembership)
        .options(joinedload(OrgMembership.user))
        .filter(OrgMembership.organization_id == org_id)
        .order_by(OrgMembership.user_identifier.asc())
        .all()
    )


def add_member(
    db: Session,
    org_id: UUID,
    login_email: str,
    role: str,
    ctx: UserContext,
    *,
    password: str | None = None,
    display_name: str | None = None,
) -> OrgMembership:
    org = get_organization(db, org_id, ctx)
    r = _validate_role_value(role, actor=ctx, db=db, org_id=org.id)
    normalized = login_email.strip().lower()
    if not normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email is required.")

    target = auth_service.get_user_by_email(db, normalized)
    if target is None:
        dn = (display_name or "").strip() or normalized.split("@", 1)[0]
        if password:
            target = auth_service.create_user(
                db,
                email=normalized,
                password=password,
                display_name=dn,
                is_root_admin=False,
            )
        else:
            temp_password = auth_service.generate_temporary_password()
            target = auth_service.create_pending_local_user(
                db,
                email=normalized,
                display_name=dn,
                temporary_password=temp_password,
            )
            invite_email_service.send_local_user_invitation_email(
                recipient_email=target.email,
                recipient_name=target.display_name,
                temporary_password=temp_password,
                expires_in_days=auth_service.TEMP_PASSWORD_EXPIRES_DAYS,
            )
    else:
        existing = (
            db.query(OrgMembership)
            .filter(
                OrgMembership.organization_id == org.id,
                OrgMembership.user_id == target.id,
            )
            .first()
        )
        if existing:
            existing.role = r
            existing.user_identifier = target.email
            db.commit()
            db.refresh(existing)
            return existing

        if target.auth_provider == "local" and bool(target.must_change_password):
            temp_password = auth_service.generate_temporary_password()
            target = auth_service.rotate_temporary_password_for_existing_local_user(
                db,
                user=target,
                temporary_password=temp_password,
            )
            invite_email_service.send_local_user_invitation_email(
                recipient_email=target.email,
                recipient_name=target.display_name,
                temporary_password=temp_password,
                expires_in_days=auth_service.TEMP_PASSWORD_EXPIRES_DAYS,
            )

    row = OrgMembership(
        organization_id=org.id,
        user_id=target.id,
        user_identifier=target.email,
        role=r,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_member(
    db: Session,
    org_id: UUID,
    user_ref: str,
    role: str,
    ctx: UserContext,
) -> OrgMembership:
    org = get_organization(db, org_id, ctx)
    r = _validate_role_value(role, actor=ctx, db=db, org_id=org.id)
    row = _membership_for_user_ref(db, org.id, user_ref)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found.")
    if not _is_root(ctx) and org_membership_role_label(ctx.role) != "org_admin" and row.role in {
        "org_admin",
        "root_admin",
    }:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not allowed to change organization or platform administrator memberships.",
        )
    row.role = r
    db.commit()
    db.refresh(row)
    return row


def remove_member(db: Session, org_id: UUID, user_ref: str, ctx: UserContext) -> None:
    org = get_organization(db, org_id, ctx)
    row = _membership_for_user_ref(db, org.id, user_ref)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found.")
    if row.role == "root_admin" and not _is_root(ctx):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only root_admin can remove a root_admin membership.",
        )
    if not _is_root(ctx) and org_membership_role_label(ctx.role) != "org_admin" and row.role in {
        "org_admin",
        "root_admin",
    }:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not allowed to remove organization or platform administrator memberships.",
        )
    db.delete(row)
    db.commit()
