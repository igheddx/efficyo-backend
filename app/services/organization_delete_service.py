"""Permanently delete an organization and all its associated data.

All FK chains use ondelete="CASCADE" at the DB level, so a single DELETE on
the organizations row cascades to:
  org_memberships, org_integrations, execution_policies, tagging_batches,
  execution_owners, approval_requests → approval_assignments,
  tenants → cloud_accounts → resource_snapshots, findings, recommendations,
  recommendation_outcomes, ingestion_jobs.

User columns (default_organization_id, default_tenant_id, default_cloud_account_id)
use ondelete="SET NULL" and are cleared automatically by the DB.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.organization import Organization


def delete_organization(db_session: Session, org_id: UUID) -> dict[str, str]:
    """Delete an organization and all cascading data. Returns the deleted org name."""
    org = db_session.query(Organization).filter(Organization.id == org_id).one_or_none()
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found.",
        )
    org_name = org.name
    db_session.query(Organization).filter(Organization.id == org_id).delete(
        synchronize_session=False
    )
    db_session.commit()
    return {"deleted_org_name": org_name}
