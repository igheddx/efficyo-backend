"""Remove ingestion-derived data for a single cloud account (fresh sync from AWS)."""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.cloud_account import CloudAccount
from app.models.finding import Finding
from app.models.ingestion_job import IngestionJob
from app.models.recommendation import Recommendation
from app.models.recommendation_outcome import RecommendationOutcome
from app.models.resource_snapshot import ResourceSnapshot
from app.services import cloud_account_service


def clear_ingested_data_for_cloud_account(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> dict[str, int]:
    """
    Delete sync jobs, resource snapshots, findings, recommendations, and outcomes
    for the given tenant + cloud account. Does not remove the cloud_accounts row.

    Order respects foreign keys (outcomes → recommendations → findings → snapshots).
    """
    cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)

    def _scope(model):
        return db_session.query(model).filter(
            model.tenant_id == tenant_id,
            model.cloud_account_id == cloud_account_id,
        )

    deleted: dict[str, int] = {}
    deleted["recommendation_outcomes"] = _scope(RecommendationOutcome).delete(synchronize_session=False)
    deleted["recommendations"] = _scope(Recommendation).delete(synchronize_session=False)
    deleted["findings"] = _scope(Finding).delete(synchronize_session=False)
    deleted["resource_snapshots"] = _scope(ResourceSnapshot).delete(synchronize_session=False)
    deleted["ingestion_jobs"] = _scope(IngestionJob).delete(synchronize_session=False)

    db_session.commit()
    return deleted


def delete_cloud_account(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> dict[str, str]:
    """
    Permanently delete a cloud account and all its associated data.

    All FK chains use ondelete="CASCADE" at the DB level, so a single DELETE on
    cloud_accounts cascades to: resource_snapshots, findings, recommendations,
    recommendation_outcomes, ingestion_jobs, approval_requests → approval_assignments.

    User columns (default_cloud_account_id) use ondelete="SET NULL" and are
    cleared automatically by the DB.
    """
    ca = (
        db_session.query(CloudAccount)
        .filter(CloudAccount.id == cloud_account_id, CloudAccount.tenant_id == tenant_id)
        .one_or_none()
    )
    if ca is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cloud account not found.",
        )
    account_name = ca.name or str(cloud_account_id)
    db_session.query(CloudAccount).filter(
        CloudAccount.id == cloud_account_id,
        CloudAccount.tenant_id == tenant_id,
    ).delete(synchronize_session=False)
    db_session.commit()
    return {"deleted_account_name": account_name}
