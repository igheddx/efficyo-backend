"""Expand provider + feature flags into a DAG of ``TaskSpec`` rows (phase-1 linear chain)."""

from __future__ import annotations

from uuid import UUID

from app.sync.enums import SyncTaskCategory
from app.sync.models import JobPlan, TaskSpec


def plan_aws_full_sync(*, job_id: UUID, cloud_account_id: UUID) -> JobPlan:
    """
    Linear scaffold: collectors → single analyzer bundle → scorer → summarizer.

    Later: parallel collector fan-out, conditional tasks, per-resource collectors.
    """
    jid = str(job_id)
    key_account_metadata = f"{jid}:aws.collector.account_metadata"
    key_inventory = f"{jid}:aws.collector.inventory"
    key_cost_usage = f"{jid}:aws.collector.cost_usage"
    key_metrics = f"{jid}:aws.collector.metrics"
    key_analyzer = f"{jid}:analyzer.bundle"
    key_scorer = f"{jid}:scorer.default"
    key_summarizer = f"{jid}:summarizer.dashboard"
    specs: list[TaskSpec] = [
        TaskSpec(
            task_category=SyncTaskCategory.COLLECTOR.value,
            task_type="aws.collector.account_metadata",
            idempotency_key=key_account_metadata,
            scope_type="cloud_account",
            scope_id=cloud_account_id,
            priority=10,
            payload={"phase": "collect"},
        ),
        TaskSpec(
            task_category=SyncTaskCategory.COLLECTOR.value,
            task_type="aws.collector.inventory",
            idempotency_key=key_inventory,
            parent_key=key_account_metadata,
            scope_type="cloud_account",
            scope_id=cloud_account_id,
            priority=20,
            payload={"phase": "collect"},
        ),
        TaskSpec(
            task_category=SyncTaskCategory.COLLECTOR.value,
            task_type="aws.collector.cost_usage",
            idempotency_key=key_cost_usage,
            parent_key=key_inventory,
            scope_type="cloud_account",
            scope_id=cloud_account_id,
            priority=30,
            payload={"phase": "collect"},
        ),
        TaskSpec(
            task_category=SyncTaskCategory.COLLECTOR.value,
            task_type="aws.collector.metrics",
            idempotency_key=key_metrics,
            parent_key=key_cost_usage,
            scope_type="cloud_account",
            scope_id=cloud_account_id,
            priority=40,
            payload={"phase": "collect"},
        ),
        TaskSpec(
            task_category=SyncTaskCategory.ANALYZER.value,
            task_type="analyzer.bundle",
            idempotency_key=key_analyzer,
            parent_key=key_metrics,
            scope_type="cloud_account",
            scope_id=cloud_account_id,
            priority=100,
            payload={"phase": "analyze"},
        ),
        TaskSpec(
            task_category=SyncTaskCategory.SCORER.value,
            task_type="scorer.default",
            idempotency_key=key_scorer,
            parent_key=key_analyzer,
            scope_type="cloud_account",
            scope_id=cloud_account_id,
            priority=200,
            payload={"phase": "score"},
        ),
        TaskSpec(
            task_category=SyncTaskCategory.SUMMARIZER.value,
            task_type="summarizer.dashboard",
            idempotency_key=key_summarizer,
            parent_key=key_scorer,
            scope_type="cloud_account",
            scope_id=cloud_account_id,
            priority=300,
            payload={"phase": "summarize"},
        ),
    ]
    critical = frozenset(
        {
            key_account_metadata,
        }
    )
    return JobPlan(task_specs=specs, critical_task_keys=critical)
