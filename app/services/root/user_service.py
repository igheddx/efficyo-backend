from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.models.organization import OrgMembership, Organization
from app.models.user import User
from app.services import auth_service

ROOT_CREATABLE_ROLES = frozenset({"org_admin", "approver", "viewer"})


def list_global_users(
    db: Session,
    *,
    search: str | None,
    org_id: UUID | None,
    role: str | None,
    status_filter: str | None,
    page: int,
    page_size: int,
) -> tuple[list[tuple[OrgMembership, User, Organization]], int]:
    q = (
        db.query(OrgMembership, User, Organization)
        .join(User, User.id == OrgMembership.user_id)
        .join(Organization, Organization.id == OrgMembership.organization_id)
        .filter(OrgMembership.user_id.isnot(None))
    )
    if org_id is not None:
        q = q.filter(OrgMembership.organization_id == org_id)
    if role and role.strip():
        q = q.filter(OrgMembership.role == role.strip().lower())
    if status_filter and status_filter.strip():
        q = q.filter(User.status == status_filter.strip().lower())
    if search and search.strip():
        term = f"%{search.strip()}%"
        q = q.filter(
            or_(
                User.email.ilike(term),
                User.display_name.ilike(term),
            )
        )
    q = q.order_by(User.email.asc(), OrgMembership.organization_id.asc())
    total = int(q.count())
    rows = q.offset((page - 1) * page_size).limit(page_size).all()
    return rows, total


def serialize_global_users(rows: list[tuple[OrgMembership, User, Organization]]) -> list[dict]:
    return [
        {
            "id": u.id,
            "full_name": u.display_name,
            "email": u.email,
            "role": m.role,
            "status": u.status or "active",
            "org_id": org.id,
            "org_name": org.name,
            "last_login_at": u.last_login_at,
            "created_at": u.created_at,
        }
        for m, u, org in rows
    ]


def create_org_user(
    db: Session,
    *,
    org_id: UUID,
    full_name: str,
    email: str,
    role: str,
    password: str,
) -> User:
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found.")
    r = role.strip().lower()
    if r not in ROOT_CREATABLE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Role must be one of: {', '.join(sorted(ROOT_CREATABLE_ROLES))}.",
        )
    normalized = email.strip().lower()
    if auth_service.get_user_by_email(db, normalized):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered.")
    dn = full_name.strip() or normalized.split("@", 1)[0]
    user = auth_service.create_user(
        db,
        email=normalized,
        password=password,
        display_name=dn,
        is_root_admin=False,
        status="active",
    )
    row = OrgMembership(
        organization_id=org.id,
        user_id=user.id,
        user_identifier=user.email,
        role=r,
    )
    db.add(row)
    db.commit()
    db.refresh(user)
    return user


def get_user_detail(db: Session, user_id: UUID) -> User:
    u = db.query(User).options(joinedload(User.memberships)).filter(User.id == user_id).first()
    if u is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return u


def user_detail_payload(db: Session, user: User) -> dict:
    memberships_raw = (
        db.query(OrgMembership, Organization)
        .join(Organization, Organization.id == OrgMembership.organization_id)
        .filter(OrgMembership.user_id == user.id)
        .order_by(Organization.name.asc())
        .all()
    )
    memberships = [
        {
            "organization_id": org.id,
            "organization_name": org.name,
            "role": m.role,
        }
        for m, org in memberships_raw
    ]
    return {
        "id": user.id,
        "full_name": user.display_name,
        "email": user.email,
        "status": user.status or "active",
        "is_root_admin": bool(user.is_root_admin),
        "last_login_at": user.last_login_at,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
        "memberships": memberships,
    }


def set_user_status(db: Session, user_id: UUID, status_value: str) -> User:
    u = get_user_detail(db, user_id)
    u.status = status_value
    db.add(u)
    db.commit()
    db.refresh(u)
    return u
