from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.recommendation import Recommendation
from app.models.sync_pipeline import SyncTask

from app.sync import repository


def summarizer_dashboard(db: Session, task: SyncTask) -> dict[str, Any]:
    """
    Phase-1 UI summary scaffold.

    This MUST be deterministic and not call LLMs. We only compute quick counts from persisted structured data.
    """

    cloud_account_id: UUID | None = task.scope_id
    if cloud_account_id is None:
        return {"summarizer": "dashboard", "status": "skipped", "reason": "missing_scope_id"}

    resolved = repository.resolve_cloud_account_org_tenant(db, cloud_account_id)
    if resolved is None:
        return {"summarizer": "dashboard", "status": "failed", "error": "cloud_account_not_found"}

    tenant_id, _org_id = resolved
    n_recs = (
        db.query(Recommendation)
        .filter(Recommendation.tenant_id == tenant_id, Recommendation.cloud_account_id == cloud_account_id)
        .count()
    )
    return {"summarizer": "dashboard", "status": "ok", "recommendations_count": n_recs}
