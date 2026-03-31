from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.sync_pipeline import SyncTask
from app.sync import repository
from app.services import ingestion_service


def handler(db: Session, task: SyncTask) -> dict[str, Any]:
    """Ingest S3 buckets into `resource_snapshots`."""
    if task.scope_type != "cloud_account" or task.scope_id is None:
        raise ValueError("invalid_task_scope")
    cloud_account_id = task.scope_id
    resolved = repository.resolve_cloud_account_org_tenant(db, cloud_account_id)
    if resolved is None:
        raise ValueError("cloud_account_not_found")
    tenant_id, _org_id = resolved
    ing = ingestion_service.ingest_s3(db, tenant_id, cloud_account_id)
    return {"collector": "s3", "ingested": ing.ingested_count}

