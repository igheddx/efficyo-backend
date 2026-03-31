"""Persist sync_job_events for observability and UI timelines."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.sync_pipeline import SyncJobEvent


def record_event(
    db: Session,
    *,
    sync_job_id: UUID,
    event_type: str,
    message: str,
    level: str = "info",
    sync_task_id: UUID | None = None,
    details: dict[str, Any] | None = None,
) -> SyncJobEvent:
    row = SyncJobEvent(
        sync_job_id=sync_job_id,
        sync_task_id=sync_task_id,
        event_type=event_type,
        level=level,
        message=message,
        details_json=details,
    )
    db.add(row)
    db.flush()
    return row
