from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.sync_pipeline import SyncTask
from app.services import ingestion_service
from app.sync import repository


def run_inventory_collection(db: Session, *, tenant_id: UUID, cloud_account_id: UUID) -> dict[str, Any]:
    """Ingest core + extended AWS inventory into ``resource_snapshots``."""

    counts: dict[str, Any] = {"ingested": {}}

    ec2 = ingestion_service.ingest_ec2(db, tenant_id, cloud_account_id)
    counts["ingested"]["ec2"] = {"count": ec2.ingested_count, "captured_at": str(ec2.captured_at)}

    ebs = ingestion_service.ingest_ebs(db, tenant_id, cloud_account_id)
    counts["ingested"]["ebs"] = {"count": ebs.ingested_count, "captured_at": str(ebs.captured_at)}

    rds = ingestion_service.ingest_rds(db, tenant_id, cloud_account_id)
    counts["ingested"]["rds"] = {"count": rds.ingested_count, "captured_at": str(rds.captured_at)}

    lam = ingestion_service.ingest_lambda(db, tenant_id, cloud_account_id)
    counts["ingested"]["lambda"] = {"count": lam.ingested_count, "captured_at": str(lam.captured_at)}

    s3 = ingestion_service.ingest_s3(db, tenant_id, cloud_account_id)
    counts["ingested"]["s3"] = {"count": s3.ingested_count, "captured_at": str(s3.captured_at)}

    ext = ingestion_service.ingest_extended_aws_services(db, tenant_id, cloud_account_id)
    counts["ingested"]["extended"] = ext

    return {"collector": "inventory", "counts": counts}


def aws_inventory_collector(db: Session, task: SyncTask) -> dict[str, Any]:
    """
    Ingest all resource snapshot types (legacy + extended AWS services).

    NOTE: Idempotency for retries still follows legacy snapshot behavior (append new captured_at batch).
    """

    cloud_account_id: UUID | None = task.scope_id
    if cloud_account_id is None:
        return {"collector": "inventory", "status": "skipped", "reason": "missing_scope_id"}

    resolved = repository.resolve_cloud_account_org_tenant(db, cloud_account_id)
    if resolved is None:
        return {"collector": "inventory", "status": "failed", "error": "cloud_account_not_found"}

    tenant_id, _org_id = resolved
    return run_inventory_collection(db, tenant_id=tenant_id, cloud_account_id=cloud_account_id)


def handler(db: Session, task: SyncTask) -> dict[str, Any]:
    """Registry-compatible alias (raises on invalid scope)."""

    if task.scope_type != "cloud_account" or task.scope_id is None:
        raise ValueError("invalid_task_scope")
    return aws_inventory_collector(db, task)
