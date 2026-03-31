from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.sync_pipeline import SyncTask
from app.models.recommendation import Recommendation

from app.sync import repository


def scorer_default(db: Session, task: SyncTask) -> dict[str, Any]:
    """
    Phase-1 scaffold: scoring is not yet persisted; UI computes computed_score dynamically.

    Deterministically returns counts for observability.
    """

    cloud_account_id: UUID | None = task.scope_id
    if cloud_account_id is None:
        return {"scorer": "default", "status": "skipped", "reason": "missing_scope_id"}
    resolved = repository.resolve_cloud_account_org_tenant(db, cloud_account_id)
    if resolved is None:
        return {"scorer": "default", "status": "failed", "error": "cloud_account_not_found"}
    tenant_id, _org_id = resolved

    n_recs = (
        db.query(Recommendation)
        .filter(Recommendation.tenant_id == tenant_id, Recommendation.cloud_account_id == cloud_account_id)
        .count()
    )
    return {"scorer": "default", "status": "ok", "recommendations_count": n_recs}
