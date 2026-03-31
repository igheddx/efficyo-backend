"""Structured logging helpers for sync jobs and tasks."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

logger = logging.getLogger("app.sync")


def job_log_extra(
    *,
    job_id: UUID | None = None,
    task_id: UUID | None = None,
    task_type: str | None = None,
    worker_id: str | None = None,
) -> dict[str, Any]:
    return {
        k: v
        for k, v in {
            "sync_job_id": str(job_id) if job_id else None,
            "sync_task_id": str(task_id) if task_id else None,
            "sync_task_type": task_type,
            "worker_id": worker_id,
        }.items()
        if v is not None
    }
