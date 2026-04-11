#!/usr/bin/env python3
"""
Sync pipeline worker (DB-backed queue).

Workers are deterministic: they only execute registered task handlers (collector/analyzer/scorer/summarizer).
Retries are task-level and bounded by `max_retries`.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from app.core.db import SessionLocal
from app.sync.queue.database import DatabaseTaskQueue
from app.sync import repository, registry, orchestrator
from app.sync.queue.base import TaskQueue
from app.services import ingestion_job_service
from app.models.ingestion_job import IngestionJob


HEARTBEAT_PATH = Path(os.environ.get("MEEZI_WORKER_HEARTBEAT_PATH", "/app/runtime/worker-heartbeat.json"))


def _write_heartbeat(*, worker_id: str, state: str, task_id: UUID | None = None) -> None:
    HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "worker_id": worker_id,
        "state": state,
        "task_id": str(task_id) if task_id is not None else None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    HEARTBEAT_PATH.write_text(json.dumps(payload), encoding="utf-8")


def _dequeue_ingestion_job(db: SessionLocal) -> UUID | None:  # type: ignore[valid-type]
    """Pick the oldest queued IngestionJob and return its id, or None if none pending."""
    job = (
        db.query(IngestionJob)
        .filter(IngestionJob.status == "queued")
        .order_by(IngestionJob.created_at.asc())
        .with_for_update(skip_locked=True)
        .first()
    )
    if job is None:
        return None
    return job.id


def main() -> int:
    worker_id = os.environ.get("SYNC_WORKER_ID") or f"sync-worker-{os.getpid()}"
    queue: TaskQueue = DatabaseTaskQueue()
    _write_heartbeat(worker_id=worker_id, state="starting")

    # Reap any jobs left in running/queued state by a previous worker process.
    # Use unconditional=True: this worker is starting fresh so the previous
    # process is dead — any active job is orphaned regardless of age.
    _startup_db = SessionLocal()
    try:
        reaped = ingestion_job_service.reap_orphaned_jobs(_startup_db, unconditional=True)
        if reaped:
            import logging
            logging.getLogger(__name__).warning("Worker startup: reaped %d orphaned sync job(s).", reaped)
    finally:
        _startup_db.close()

    # Worker loop
    while True:
        db = SessionLocal()
        try:
            task_id = queue.dequeue_task(db, worker_id=worker_id)
            if task_id is None:
                # No new-pipeline tasks — check for queued legacy IngestionJobs.
                ingestion_job_id = _dequeue_ingestion_job(db)
                if ingestion_job_id is not None:
                    db.close()
                    _write_heartbeat(worker_id=worker_id, state="processing")
                    ingestion_job_service.execute_sync_job(ingestion_job_id)
                    _write_heartbeat(worker_id=worker_id, state="idle")
                else:
                    _write_heartbeat(worker_id=worker_id, state="idle")
                    db.close()
                    time.sleep(1.5)
                continue

            task = repository.get_task(db, task_id)  # type: ignore[arg-type]
            if task is None:
                _write_heartbeat(worker_id=worker_id, state="idle")
                db.close()
                continue

            _write_heartbeat(worker_id=worker_id, state="processing", task_id=task.id)

            # Execute task handler (registry updates task.status/result/error fields)
            success, _err_code, _err_msg = registry.run_task(db, task)

            db.commit()

            # If failed, schedule retry (queue enforces max_retries).
            if not success and task.status == "failed":
                queue.schedule_retry(
                    db,
                    task_id=task.id,
                    delay_seconds=int(os.environ.get("SYNC_RETRY_DELAY_SECONDS", "30")),
                    error_code=task.error_code,
                    error_message=task.error_message,
                )
                db.commit()

            # Update job terminal / phase based on tasks.
            orchestrator.evaluate_job_after_tasks(db, task.sync_job_id)
            db.commit()
            _write_heartbeat(worker_id=worker_id, state="idle")
        finally:
            try:
                db.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())

