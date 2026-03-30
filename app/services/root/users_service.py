from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.models.organization import OrgMembership, Organization
from app.models.user import User
from app.services import auth_service


def list_root_global_users(
    db: Session,
    *,
    page: int,
    page_size: int,
    search: str | None,
    org_id: UUID | None,
    role: str | None,
    user_status: str | None,
) -> tuple[list[dict], int]:
    q = (
        db.query(OrgMembership)
        .options(joinedload(OrgMembership.user), joinedload(OrgMembership.organization))
        .join(Organization, Organization.id == OrgMembership.organization_id)
    )
    q = q.outerjoin(User, User.id == OrgMembership.user_id)
    if org_id is not None:
        q = q.filter(OrgMembership.organization_id == org_id)
    if role and role.strip():
        q = q.filter(OrgMembership.role == role.strip().lower())
    if user_status and user_status.strip():
        st = user_status.strip().lower()
        q = q.filter(or_(OrgMembership.user_id.is_(None), User.status == st))
    if search and search.strip():
        term = f"%{search.strip()}%"
        q = q.filter(
            or_(
                OrgMembership.user_identifier.ilike(term),
                User.email.ilike(term),
                User.display_name.ilike(term),
            )
        )
    total = q.count()
    rows = (
        q.order_by(Organization.name.asc(), OrgMembership.user_identifier.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    out: list[dict] = []
    for m in rows:
        u = m.user
        email = u.email if u is not None else m.user_identifier
        display_name = u.display_name if u is not None else None
        ustatus = (u.status if u is not None else "active") or "active"
        org = m.organization
        out.append(
            {
                "membership_id": m.id,
                "user_id": m.user_id,
                "email": email,
                "display_name": display_name,
                "role": m.role,
                "user_status": ustatus,
                "organization_id": m.organization_id,
                "organization_name": org.name if org is not None else "",
                "last_login_at": u.last_login_at if u is not None else None,
                "created_at": m.created_at,
            }
        )
    return out, total


def root_create_org_user(
    db: Session,
    *,
    organization_id: UUID,
    email: str,
    password: str,
    display_name: str | None,
    role: str,
) -> dict:
    r = role.strip().lower()
    if r not in {"org_admin", "approver", "viewer"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="role must be org_admin, approver, or viewer",
        )
    org = db.query(Organization).filter(Organization.id == organization_id).first()
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found.")
    normalized = email.strip().lower()
    if not normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email is required.")
    target = auth_service.get_user_by_email(db, normalized)
    if target is None:
        dn = (display_name or "").strip() or normalized.split("@", 1)[0]
        target = auth_service.create_user(
            db,
            email=normalized,
            password=password,
            display_name=dn,
            is_root_admin=False,
        )
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
        m = existing
    else:
        m = OrgMembership(
            organization_id=org.id,
            user_id=target.id,
            user_identifier=target.email,
            role=r,
        )
        db.add(m)
        db.commit()
        db.refresh(m)
    u = target
    return {
        "membership_id": m.id,
        "user_id": m.user_id,
        "email": u.email,
        "display_name": u.display_name,
        "role": m.role,
        "user_status": u.status or "active",
        "organization_id": m.organization_id,
        "organization_name": org.name,
        "last_login_at": u.last_login_at,
        "created_at": m.created_at,
    }


def get_root_user_detail(db: Session, user_id: UUID) -> dict:
    u = db.query(User).filter(User.id == user_id).first()
    if u is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    mems = (
        db.query(OrgMembership)
        .options(joinedload(OrgMembership.organization))
        .filter(OrgMembership.user_id == user_id)
        .order_by(OrgMembership.created_at.asc())
        .all()
    )
    brief = []
    for m in mems:
        oname = m.organization.name if m.organization is not None else ""
        brief.append(
            {
                "organization_id": m.organization_id,
                "organization_name": oname,
                "role": m.role,
                "created_at": m.created_at,
            }
        )
    return {
        "id": u.id,
        "email": u.email,
        "display_name": u.display_name,
        "status": u.status or "active",
        "is_root_admin": u.is_root_admin,
        "last_login_at": u.last_login_at,
        "created_at": u.created_at,
        "updated_at": u.updated_at,
        "memberships": brief,
    }


def patch_root_user_status(db: Session, user_id: UUID, *, new_status: str) -> dict:
    u = db.query(User).filter(User.id == user_id).first()
    if u is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    u.status = new_status
    db.commit()
    db.refresh(u)
    return get_root_user_detail(db, user_id)
