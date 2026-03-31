"""DB access for sync_jobs / sync_tasks / sync_job_events."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import desc, exists, func, or_, select
from sqlalchemy.orm import Session

from app.models.cloud_account import CloudAccount
from app.models.tenant import Tenant
from app.models.sync_pipeline import SyncJob, SyncJobEvent, SyncTask
from app.sync.enums import ACTIVE_JOB_STATUSES, TERMINAL_TASK_STATUSES


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_job(db: Session, job_id: UUID) -> SyncJob | None:
    return db.query(SyncJob).filter(SyncJob.id == job_id).first()


def get_task(db: Session, task_id: UUID) -> SyncTask | None:
    return db.query(SyncTask).filter(SyncTask.id == task_id).first()


def count_active_jobs_for_scope(
    db: Session,
    *,
    tenant_id: UUID,
    cloud_account_id: UUID,
    provider: str,
) -> int:
    return (
        db.query(func.count(SyncJob.id))
        .filter(
            SyncJob.tenant_id == tenant_id,
            SyncJob.cloud_account_id == cloud_account_id,
            SyncJob.provider == provider,
            SyncJob.status.in_(ACTIVE_JOB_STATUSES),
        )
        .scalar()
        or 0
    )


def list_jobs(
    db: Session,
    *,
    organization_id: UUID | None = None,
    tenant_id: UUID | None = None,
    cloud_account_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[SyncJob], int]:
    q = db.query(SyncJob)
    if organization_id is not None:
        q = q.filter(SyncJob.organization_id == organization_id)
    if tenant_id is not None:
        q = q.filter(SyncJob.tenant_id == tenant_id)
    if cloud_account_id is not None:
        q = q.filter(SyncJob.cloud_account_id == cloud_account_id)
    total = q.count()
    rows = q.order_by(desc(SyncJob.created_at)).offset(offset).limit(limit).all()
    return rows, total


def list_tasks_for_job(db: Session, job_id: UUID) -> list[SyncTask]:
    return (
        db.query(SyncTask)
        .filter(SyncTask.sync_job_id == job_id)
        .order_by(SyncTask.priority.asc(), SyncTask.created_at.asc())
        .all()
    )


def list_events_for_job(db: Session, job_id: UUID, limit: int = 200) -> list[SyncJobEvent]:
    return (
        db.query(SyncJobEvent)
        .filter(SyncJobEvent.sync_job_id == job_id)
        .order_by(desc(SyncJobEvent.created_at))
        .limit(limit)
        .all()
    )


def claim_next_queued_task(db: Session, *, worker_id: str) -> SyncTask | None:
    """Claim one queued task. Uses ``SKIP LOCKED`` on Postgres; plain claim on SQLite."""
    now = utcnow()
    # Dependency gating: when tasks declare a `parent_task_id`, only claim tasks whose
    # parent is terminal. Use EXISTS instead of OUTER JOIN so Postgres can apply
    # FOR UPDATE SKIP LOCKED safely.
    from sqlalchemy.orm import aliased

    Parent = aliased(SyncTask)
    parent_not_terminal = exists(
        select(1).where(
            Parent.id == SyncTask.parent_task_id,
            Parent.status.not_in(TERMINAL_TASK_STATUSES),
        )
    )
    q = (
        db.query(SyncTask)
        .filter(SyncTask.status == "queued")
        .filter(or_(SyncTask.scheduled_at.is_(None), SyncTask.scheduled_at <= now))
        .filter(~parent_not_terminal)
        .order_by(SyncTask.priority.asc(), SyncTask.created_at.asc())
    )
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        task = q.with_for_update(skip_locked=True).first()
    else:
        task = q.first()
    if task is None:
        return None
    task.status = "running"
    task.worker_id = worker_id
    task.started_at = utcnow()
    task.last_heartbeat_at = utcnow()
    db.flush()
    return task


def refresh_job_counters(db: Session, job_id: UUID) -> None:
    """Recompute completed/failed/skipped from tasks."""
    job = get_job(db, job_id)
    if job is None:
        return
    tasks = list_tasks_for_job(db, job_id)
    job.total_tasks = len(tasks)
    job.completed_tasks = sum(1 for t in tasks if t.status == "succeeded")
    job.failed_tasks = sum(1 for t in tasks if t.status == "failed")
    job.skipped_tasks = sum(1 for t in tasks if t.status in ("skipped", "cancelled"))
    db.flush()


def resolve_cloud_account_org_tenant(
    db: Session, cloud_account_id: UUID
) -> tuple[UUID, UUID] | None:
    """Return (tenant_id, organization_id) for a cloud account."""
    row = (
        db.query(CloudAccount, Tenant.organization_id)
        .join(Tenant, Tenant.id == CloudAccount.tenant_id)
        .filter(CloudAccount.id == cloud_account_id)
        .first()
    )
    if row is None:
        return None
    ca, org_id = row[0], row[1]
    if org_id is None:
        return None
    return ca.tenant_id, org_id
