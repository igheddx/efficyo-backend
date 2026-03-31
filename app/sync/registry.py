"""
task_type → handler registry.

Handlers are **deterministic** callables: ``(db, task) -> result_dict``.
They must be idempotent for the same ``(sync_job_id, idempotency_key)``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.models.sync_pipeline import SyncTask

TaskHandler = Callable[[Session, SyncTask], dict[str, Any]]

TASK_REGISTRY: dict[str, TaskHandler] = {}


def register_task(task_type: str, handler: TaskHandler) -> None:
    TASK_REGISTRY[task_type] = handler


def get_handler(task_type: str) -> TaskHandler | None:
    return TASK_REGISTRY.get(task_type)


def _stub_collector(name: str) -> TaskHandler:
    def _run(db: Session, task: SyncTask) -> dict[str, Any]:
        return {"collector": name, "status": "stub", "job_id": str(task.sync_job_id)}

    return _run


def load_default_handlers() -> None:
    """Register scaffold handlers (replace with real AWS collectors / analyzers incrementally)."""
    if TASK_REGISTRY:
        return

    # Providers (AWS)
    from app.providers.aws.collectors.account_metadata import aws_account_metadata_collector
    from app.providers.aws.collectors.inventory import aws_inventory_collector
    from app.providers.aws.collectors.cost_usage import aws_cost_usage_collector
    from app.providers.aws.collectors.metrics import aws_metrics_collector

    # Domain pipeline
    from app.analyzers.bundle import analyzer_bundle

    # Deterministic, non-AI scoring + UI summaries for now.
    from app.recommendations.scoring import scorer_default
    from app.copilot.summary_adapter import summarizer_dashboard

    register_task("aws.collector.account_metadata", aws_account_metadata_collector)
    register_task("aws.collector.inventory", aws_inventory_collector)
    register_task("aws.collector.cost_usage", aws_cost_usage_collector)
    register_task("aws.collector.metrics", aws_metrics_collector)
    register_task("analyzer.bundle", analyzer_bundle)
    register_task("scorer.default", scorer_default)
    register_task("summarizer.dashboard", summarizer_dashboard)


def run_task(db: Session, task: SyncTask) -> tuple[bool, str | None, str | None]:
    """Execute handler; update task row with success or structured failure.

    Returns: (success, error_code, error_message)
    """
    from app.sync import repository
    from app.sync.enums import SyncErrorCode

    load_default_handlers()
    handler = get_handler(task.task_type)
    if handler is None:
        task.status = "failed"
        task.failed_at = repository.utcnow()
        task.error_code = SyncErrorCode.VALIDATION_ERROR.value
        task.error_message = f"Unregistered task_type: {task.task_type}"
        return False, task.error_code, task.error_message
    try:
        result = handler(db, task)
        task.status = "succeeded"
        task.result_json = result
        task.completed_at = repository.utcnow()
        task.error_code = None
        task.error_message = None
        from app.sync.events import record_event

        record_event(
            db,
            sync_job_id=task.sync_job_id,
            sync_task_id=task.id,
            event_type="task.succeeded",
            level="info",
            message=f"Task succeeded: {task.task_type}",
            details={"task_type": task.task_type, "idempotency_key": task.idempotency_key},
        )
        return True, None, None
    except Exception as exc:  # noqa: BLE001 — boundary: persist error on task
        task.status = "failed"
        task.failed_at = repository.utcnow()
        task.error_code = SyncErrorCode.UNKNOWN_ERROR.value
        task.error_message = str(exc)[:4000]
        from app.sync.events import record_event

        record_event(
            db,
            sync_job_id=task.sync_job_id,
            sync_task_id=task.id,
            event_type="task.failed",
            level="error",
            message=f"Task failed: {task.task_type}",
            details={
                "task_type": task.task_type,
                "idempotency_key": task.idempotency_key,
                "error_message": task.error_message,
            },
        )
        return False, task.error_code, task.error_message
