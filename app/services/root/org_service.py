from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.models.cloud_account import CloudAccount
from app.models.ingestion_job import IngestionJob
from app.models.organization import OrgMembership, Organization
from app.models.recommendation_outcome import RecommendationOutcome
from app.models.tenant import Tenant
from app.models.user import User


def _user_counts_by_org(db: Session) -> dict[UUID, int]:
    rows = (
        db.query(OrgMembership.organization_id, func.count(OrgMembership.id))
        .group_by(OrgMembership.organization_id)
        .all()
    )
    return {oid: int(c) for oid, c in rows}


def _aws_counts_by_org(db: Session) -> dict[UUID, int]:
    rows = (
        db.query(Tenant.organization_id, func.count(CloudAccount.id))
        .join(CloudAccount, CloudAccount.tenant_id == Tenant.id)
        .filter(Tenant.organization_id.isnot(None))
        .group_by(Tenant.organization_id)
        .all()
    )
    return {oid: int(c) for oid, c in rows if oid is not None}


def _last_activity_by_org(db: Session) -> dict[UUID, object]:
    rows = (
        db.query(Tenant.organization_id, func.max(IngestionJob.completed_at))
        .join(IngestionJob, IngestionJob.tenant_id == Tenant.id)
        .filter(Tenant.organization_id.isnot(None))
        .group_by(Tenant.organization_id)
        .all()
    )
    return {oid: ts for oid, ts in rows if oid is not None}


def list_orgs(
    db: Session,
    *,
    search: str | None,
    status_filter: str | None,
    page: int,
    page_size: int,
    sort_by: str,
    sort_order: str,
) -> tuple[list[Organization], int]:
    q = db.query(Organization)
    if search and search.strip():
        term = f"%{search.strip()}%"
        q = q.filter(or_(Organization.name.ilike(term), Organization.slug.ilike(term)))
    if status_filter and status_filter.strip():
        q = q.filter(Organization.status == status_filter.strip().lower())

    sort_map = {
        "name": Organization.name,
        "slug": Organization.slug,
        "created_at": Organization.created_at,
        "updated_at": Organization.updated_at,
        "status": Organization.status,
    }
    col = sort_map.get(sort_by, Organization.name)
    if sort_order.lower() == "desc":
        q = q.order_by(col.desc(), Organization.id.asc())
    else:
        q = q.order_by(col.asc(), Organization.id.asc())

    total = int(q.count())
    rows = q.offset((page - 1) * page_size).limit(page_size).all()
    return rows, total


def serialize_org_list_rows(db: Session, orgs: list[Organization]) -> list[dict]:
    uc = _user_counts_by_org(db)
    ac = _aws_counts_by_org(db)
    la = _last_activity_by_org(db)
    out: list[dict] = []
    for o in orgs:
        out.append(
            {
                "id": o.id,
                "name": o.name,
                "slug": o.slug,
                "status": o.status or "active",
                "created_at": o.created_at,
                "updated_at": o.updated_at,
                "user_count": uc.get(o.id, 0),
                "aws_account_count": ac.get(o.id, 0),
                "last_activity_at": la.get(o.id),
            }
        )
    return out


def get_org(db: Session, org_id: UUID) -> Organization:
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found.")
    return org


def org_detail_payload(db: Session, org: Organization) -> dict:
    tenant_ids = [r[0] for r in db.query(Tenant.id).filter(Tenant.organization_id == org.id).all()]
    user_count = int(
        db.query(func.count(OrgMembership.id)).filter(OrgMembership.organization_id == org.id).scalar() or 0
    )
    aws_count = 0
    pending = 0
    last_scan = None
    if tenant_ids:
        aws_count = int(
            db.query(func.count(CloudAccount.id))
            .join(Tenant, Tenant.id == CloudAccount.tenant_id)
            .filter(Tenant.organization_id == org.id)
            .scalar()
            or 0
        )
        pending = int(
            db.query(func.count(RecommendationOutcome.id))
            .filter(
                RecommendationOutcome.tenant_id.in_(tenant_ids),
                RecommendationOutcome.workflow_status == "suggested",
            )
            .scalar()
            or 0
        )
        last_scan = (
            db.query(func.max(IngestionJob.completed_at))
            .join(Tenant, Tenant.id == IngestionJob.tenant_id)
            .filter(Tenant.organization_id == org.id, IngestionJob.completed_at.isnot(None))
            .scalar()
        )
    return {
        "id": org.id,
        "name": org.name,
        "slug": org.slug,
        "status": org.status or "active",
        "created_at": org.created_at,
        "updated_at": org.updated_at,
        "summary": {
            "user_count": user_count,
            "aws_account_count": aws_count,
            "pending_approvals": pending,
            "last_scan_at": last_scan,
        },
    }


def create_org(db: Session, *, name: str, slug: str) -> Organization:
    slug_norm = slug.strip().lower()
    exists = db.query(Organization.id).filter(Organization.slug == slug_norm).first()
    if exists:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Slug already in use.")
    trimmed = name.strip()
    if not trimmed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name is required.")
    org = Organization(name=trimmed, slug=slug_norm, status="active")
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def set_org_status(db: Session, org_id: UUID, status_value: str) -> Organization:
    org = get_org(db, org_id)
    org.status = status_value
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def list_org_users(
    db: Session,
    org_id: UUID,
    *,
    page: int,
    page_size: int,
) -> tuple[list[OrgMembership], int]:
    get_org(db, org_id)
    q = (
        db.query(OrgMembership)
        .options(joinedload(OrgMembership.user))
        .filter(OrgMembership.organization_id == org_id, OrgMembership.user_id.isnot(None))
        .order_by(OrgMembership.user_identifier.asc())
    )
    total = int(q.count())
    rows = q.offset((page - 1) * page_size).limit(page_size).all()
    return rows, total


def serialize_org_users(rows: list[OrgMembership]) -> list[dict]:
    out: list[dict] = []
    for m in rows:
        u = m.user
        if u is None:
            continue
        out.append(
            {
                "id": u.id,
                "full_name": u.display_name,
                "email": u.email,
                "role": m.role,
                "status": u.status or "active",
                "last_login_at": u.last_login_at,
                "created_at": u.created_at,
            }
        )
    return out
