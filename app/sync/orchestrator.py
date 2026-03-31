"""Job lifecycle: create → plan → dispatch → evaluate terminal state."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.sync_pipeline import SyncJob
from app.sync import dispatcher, events, planner, repository
from app.sync.enums import (
    ACTIVE_JOB_STATUSES,
    CRITICAL_ERROR_CODES,
    SyncJobStatus,
    SyncTaskStatus,
    TERMINAL_TASK_STATUSES,
)
from app.sync.queue.base import TaskQueue


def _task_terminal(status: str) -> bool:
    return status in TERMINAL_TASK_STATUSES


def evaluate_job_after_tasks(db: Session, job_id: UUID) -> SyncJob | None:
    """
    If all tasks are terminal, set job to completed / completed_with_errors / failed.

    Failed **critical** idempotency keys (from planner) → ``failed``.
    Any other failure with at least one success → ``completed_with_errors``.
    """
    job = repository.get_job(db, job_id)
    if job is None:
        return None
    tasks = repository.list_tasks_for_job(db, job_id)
    if not tasks:
        return job

    # Phase progression for UI polling:
    # - If any collector tasks are still in-flight, keep the job in "collecting"
    # - Else advance to analyzing/scoring/summarizing as subsequent categories run.
    if any(
        (t.task_category == "collector") and not _task_terminal(t.status)  # type: ignore[comparison-overlap]
        for t in tasks
    ):
        job.status = SyncJobStatus.COLLECTING.value
    elif any((t.task_category == "analyzer") and not _task_terminal(t.status) for t in tasks):
        job.status = SyncJobStatus.ANALYZING.value
    elif any((t.task_category == "scorer") and not _task_terminal(t.status) for t in tasks):
        job.status = SyncJobStatus.SCORING.value
    elif any((t.task_category == "summarizer") and not _task_terminal(t.status) for t in tasks):
        job.status = SyncJobStatus.SUMMARIZING.value

    if not all(_task_terminal(t.status) for t in tasks):
        repository.refresh_job_counters(db, job_id)
        job.last_heartbeat_at = repository.utcnow()
        return job

    summary = job.summary_json or {}
    critical_keys: set[str] = set(summary.get("critical_task_keys") or [])
    by_key = {t.idempotency_key: t for t in tasks}

    failed = [t for t in tasks if t.status == SyncTaskStatus.FAILED.value]
    critical_failed = [t for t in failed if t.idempotency_key in critical_keys]
    auth_like = [
        t
        for t in failed
        if (t.error_code or "") in CRITICAL_ERROR_CODES
    ]

    if critical_failed or auth_like:
        job.status = SyncJobStatus.FAILED.value
        job.failed_at = repository.utcnow()
        job.error_summary = _summarize_failures(failed)
    elif failed:
        job.status = SyncJobStatus.COMPLETED_WITH_ERRORS.value
        job.completed_at = repository.utcnow()
        job.error_summary = _summarize_failures(failed)
    else:
        job.status = SyncJobStatus.COMPLETED.value
        job.completed_at = repository.utcnow()
        job.error_summary = None

    repository.refresh_job_counters(db, job_id)
    job.last_heartbeat_at = repository.utcnow()
    events.record_event(
        db,
        sync_job_id=job.id,
        event_type="job.terminal",
        message=f"Job finished as {job.status}",
        details={"failed_tasks": len(failed)},
    )
    return job


def _summarize_failures(tasks: list[Any]) -> str:
    parts = [f"{t.task_type}: {t.error_code or 'error'} — {(t.error_message or '')[:200]}" for t in tasks]
    return "; ".join(parts)[:4000]


def create_job_with_plan(
    db: Session,
    *,
    organization_id: UUID,
    tenant_id: UUID,
    cloud_account_id: UUID,
    provider: str,
    initiated_by_user_id: UUID | None,
    trigger_type: str,
    force_new: bool,
    queue: TaskQueue,
) -> SyncJob:
    """Create job, run planner, insert tasks, move job into ``collecting`` phase."""
    if not force_new:
        n = repository.count_active_jobs_for_scope(
            db,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            provider=provider,
        )
        if n > 0:
            raise ValueError("active_sync_job_exists")

    job = SyncJob(
        organization_id=organization_id,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        provider=provider,
        initiated_by_user_id=initiated_by_user_id,
        trigger_type=trigger_type,
        status=SyncJobStatus.PLANNING.value,
        force_new=force_new,
        summary_json={},
    )
    db.add(job)
    db.flush()

    events.record_event(
        db,
        sync_job_id=job.id,
        event_type="job.created",
        message="Sync job created",
        details={"provider": provider, "trigger": trigger_type},
    )

    plan = planner.plan_aws_full_sync(job_id=job.id, cloud_account_id=cloud_account_id)
    job.summary_json = {
        "critical_task_keys": list(plan.critical_task_keys),
        "planned_task_count": len(plan.task_specs),
    }

    dispatcher.persist_planned_tasks(db, sync_job_id=job.id, plan=plan, queue=queue, provider=provider)

    job.status = SyncJobStatus.COLLECTING.value
    job.started_at = repository.utcnow()
    repository.refresh_job_counters(db, job.id)

    events.record_event(
        db,
        sync_job_id=job.id,
        event_type="job.dispatched",
        message=f"Planned {len(plan.task_specs)} tasks",
        details={"total_tasks": len(plan.task_specs)},
    )
    return job
