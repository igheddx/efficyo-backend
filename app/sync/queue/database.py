"""Durable queue backed by ``sync_tasks`` rows (poll + claim)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.sync import repository
from app.sync.logging import job_log_extra, logger


class DatabaseTaskQueue:
    """
    Tasks are durable rows; ``enqueue`` is a no-op because planner already inserted ``queued`` rows.

    ``dequeue`` claims the next row. For horizontal scale, run multiple workers against Postgres
    ``SKIP LOCKED`` (see ``repository.claim_next_queued_task``).
    """

    def enqueue_task(self, db: Session, *, task_id: UUID, task_type: str, payload: dict[str, Any]) -> None:
        logger.debug("queue.enqueue_task (db noop; task already persisted)", extra=job_log_extra(task_id=task_id))

    def dequeue_task(self, db: Session, *, worker_id: str) -> UUID | None:
        task = repository.claim_next_queued_task(db, worker_id=worker_id)
        if task is None:
            return None
        db.flush()
        return task.id

    def schedule_retry(
        self,
        db: Session,
        *,
        task_id: UUID,
        delay_seconds: int = 0,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        task = repository.get_task(db, task_id)
        if task is None:
            return
        if task.retry_count >= task.max_retries:
            task.status = "failed"
            task.failed_at = repository.utcnow()
            task.error_code = error_code or "unknown_error"
            task.error_message = error_message
            task.worker_id = None
            db.flush()
            return
        task.retry_count += 1
        task.status = "retrying"
        task.error_code = error_code
        task.error_message = error_message
        task.failed_at = None
        db.flush()
        task.status = "queued"
        task.worker_id = None
        if delay_seconds > 0:
            task.scheduled_at = repository.utcnow() + timedelta(seconds=delay_seconds)
        db.flush()

    def publish_event(self, db: Session, *, event_type: str, payload: dict[str, Any]) -> None:
        logger.info("queue.publish_event %s", event_type, extra={"payload_keys": list(payload.keys())})
