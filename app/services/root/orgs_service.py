from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.org_slug import ensure_unique_org_slug, slugify_name
from app.models.cloud_account import CloudAccount
from app.models.ingestion_job import IngestionJob
from app.models.organization import Organization, OrgMembership
from app.models.recommendation_outcome import RecommendationOutcome
from app.models.tenant import Tenant


def _aws_account_count(db: Session, org_id: UUID) -> int:
    return (
        db.query(func.count(CloudAccount.id))
        .join(Tenant, Tenant.id == CloudAccount.tenant_id)
        .filter(Tenant.organization_id == org_id)
        .scalar()
        or 0
    )


def _last_scan_at(db: Session, org_id: UUID):
    return (
        db.query(func.max(func.coalesce(IngestionJob.completed_at, IngestionJob.created_at)))
        .join(CloudAccount, CloudAccount.id == IngestionJob.cloud_account_id)
        .join(Tenant, Tenant.id == CloudAccount.tenant_id)
        .filter(Tenant.organization_id == org_id)
        .scalar()
    )


def _pending_approvals_org(db: Session, org_id: UUID) -> int:
    return (
        db.query(func.count(RecommendationOutcome.id))
        .join(CloudAccount, CloudAccount.id == RecommendationOutcome.cloud_account_id)
        .join(Tenant, Tenant.id == CloudAccount.tenant_id)
        .filter(
            Tenant.organization_id == org_id,
            RecommendationOutcome.workflow_status == "suggested",
        )
        .scalar()
        or 0
    )


def _user_count(db: Session, org_id: UUID) -> int:
    return (
        db.query(func.count(OrgMembership.id))
        .filter(OrgMembership.organization_id == org_id)
        .scalar()
        or 0
    )


def _org_to_row(db: Session, org: Organization) -> dict:
    oid = org.id
    return {
        "id": org.id,
        "name": org.name,
        "slug": org.slug,
        "status": org.status or "active",
        "user_count": _user_count(db, oid),
        "aws_account_count": _aws_account_count(db, oid),
        "last_scan_at": _last_scan_at(db, oid),
        "pending_approvals": _pending_approvals_org(db, oid),
        "created_at": org.created_at,
        "updated_at": org.updated_at,
    }


def list_root_organizations(
    db: Session,
    *,
    page: int,
    page_size: int,
    search: str | None,
    status_filter: str | None,
) -> tuple[list[dict], int]:
    q = db.query(Organization)
    if status_filter:
        q = q.filter(Organization.status == status_filter)
    if search and search.strip():
        term = f"%{search.strip()}%"
        q = q.filter(or_(Organization.name.ilike(term), Organization.slug.ilike(term)))
    total = q.count()
    rows = (
        q.order_by(Organization.name.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return [_org_to_row(db, o) for o in rows], total


def create_root_organization(db: Session, *, name: str, slug: str | None) -> Organization:
    trimmed = name.strip()
    if not trimmed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name is required.")
    if slug is not None and slug.strip():
        base = slugify_name(slug.strip())
        if not base:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid slug.")
        taken = db.query(Organization.id).filter(Organization.slug == base).first()
        if taken is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Organization slug already exists.",
            )
        final_slug = base[:255]
    else:
        final_slug = ensure_unique_org_slug(db, slugify_name(trimmed))
    org = Organization(name=trimmed, slug=final_slug, status="active")
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def get_root_organization(db: Session, org_id: UUID) -> dict:
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found.")
    return _org_to_row(db, org)


def patch_root_organization_status(db: Session, org_id: UUID, *, new_status: str) -> dict:
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found.")
    org.status = new_status
    db.commit()
    db.refresh(org)
    return _org_to_row(db, org)
