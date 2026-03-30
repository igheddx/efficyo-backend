from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.db import utc_now
from app.models.access_grant import AccessGrant
from app.models.cloud_account import CloudAccount
from app.models.tenant import Tenant
from app.services import context_defaults_service


def _valid_access_role(r: str) -> str:
    x = (r or "").strip().lower()
    if x not in ("viewer", "approver", "admin"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="access_role must be viewer, approver, or admin.",
        )
    return x


def list_grants_for_org(db: Session, organization_id: UUID) -> list[AccessGrant]:
    return (
        db.query(AccessGrant)
        .filter(AccessGrant.organization_id == organization_id)
        .order_by(AccessGrant.created_at.desc())
        .all()
    )


def create_grant(
    db: Session,
    *,
    organization_id: UUID,
    user_id: UUID,
    tenant_id: UUID,
    cloud_account_id: UUID | None,
    access_role: str,
) -> AccessGrant:
    ar = _valid_access_role(access_role)
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant is None or tenant.organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant is not in this organization.",
        )
    if cloud_account_id is not None:
        ca = (
            db.query(CloudAccount)
            .filter(CloudAccount.id == cloud_account_id, CloudAccount.tenant_id == tenant_id)
            .first()
        )
        if ca is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cloud account not found for this tenant.",
            )

    existing = (
        db.query(AccessGrant)
        .filter(
            AccessGrant.user_id == user_id,
            AccessGrant.organization_id == organization_id,
            AccessGrant.tenant_id == tenant_id,
            AccessGrant.cloud_account_id == cloud_account_id,
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A grant already exists for this user, tenant, and scope.",
        )

    row = AccessGrant(
        user_id=user_id,
        organization_id=organization_id,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        access_role=ar,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    context_defaults_service.maybe_seed_defaults_on_first_grant(
        db,
        user_id=user_id,
        organization_id=organization_id,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
    )
    return row


def update_grant(
    db: Session,
    *,
    organization_id: UUID,
    grant_id: UUID,
    access_role: str,
) -> AccessGrant:
    ar = _valid_access_role(access_role)
    row = (
        db.query(AccessGrant)
        .filter(AccessGrant.id == grant_id, AccessGrant.organization_id == organization_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grant not found.")
    row.access_role = ar
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def delete_grant(db: Session, *, organization_id: UUID, grant_id: UUID) -> None:
    row = (
        db.query(AccessGrant)
        .filter(AccessGrant.id == grant_id, AccessGrant.organization_id == organization_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grant not found.")
    db.delete(row)
    db.commit()
