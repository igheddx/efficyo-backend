"""Persist planned tasks and optionally notify the queue backend."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.sync_pipeline import SyncTask
from app.sync.models import JobPlan, TaskSpec
from app.sync.queue.base import TaskQueue


def persist_planned_tasks(
    db: Session,
    *,
    sync_job_id: UUID,
    plan: JobPlan,
    queue: TaskQueue,
    provider: str = "aws",
) -> list[SyncTask]:
    rows: list[SyncTask] = []
    parent_by_key: dict[str, UUID] = {}
    for spec in plan.task_specs:
        parent_id = None
        if spec.parent_key:
            parent_id = parent_by_key.get(spec.parent_key)
        row = SyncTask(
            sync_job_id=sync_job_id,
            parent_task_id=parent_id,
            task_category=spec.task_category,
            task_type=spec.task_type,
            provider=spec.provider or provider,
            scope_type=spec.scope_type,
            scope_id=spec.scope_id,
            idempotency_key=spec.idempotency_key,
            status="queued",
            priority=spec.priority,
            max_retries=spec.max_retries,
            payload_json=spec.payload or {},
        )
        db.add(row)
        db.flush()
        queue.enqueue_task(db, task_id=row.id, task_type=row.task_type, payload=spec.payload)
        rows.append(row)
        parent_by_key[spec.idempotency_key] = row.id
    return rows
