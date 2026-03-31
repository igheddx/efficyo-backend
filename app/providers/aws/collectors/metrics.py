from __future__ import annotations
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.sync_pipeline import SyncTask


def aws_metrics_collector(_db: Session, task: SyncTask) -> dict[str, Any]:
    """Phase-1 scaffold: metrics aren’t modeled separately yet (legacy uses cost/explorer + tags)."""
    return {"collector": "metrics", "status": "skipped", "task_id": str(task.id)}

from typing import Any

from sqlalchemy.orm import Session

from app.models.sync_pipeline import SyncTask


def handler(db: Session, task: SyncTask) -> dict[str, Any]:
    """Metrics collection placeholder (phase-1 scaffold)."""

    _ = (db, task)
    return {"collector": "metrics", "status": "stub"}

