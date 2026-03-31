from __future__ import annotations
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.sync_pipeline import SyncTask


def aws_cost_usage_collector(_db: Session, task: SyncTask) -> dict[str, Any]:
    """Phase-1 scaffold. In v1 we compute cost inside summary endpoints; keep collector deterministic no-op."""
    return {"collector": "cost_usage", "status": "skipped", "task_id": str(task.id)}

from typing import Any

from sqlalchemy.orm import Session

from app.models.sync_pipeline import SyncTask


def handler(db: Session, task: SyncTask) -> dict[str, Any]:
    """
    Cost-usage collection placeholder.

    Phase-1 scaffold keeps determinism by reusing the existing recommendation/savings estimators.
    When we add `billing_snapshots` / `metrics_snapshots`, this task will persist them.
    """

    _ = (db, task)
    return {"collector": "cost_usage", "status": "stub"}

