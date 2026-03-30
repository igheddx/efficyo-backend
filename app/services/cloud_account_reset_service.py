"""Remove ingestion-derived data for a single cloud account (fresh sync from AWS)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

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
