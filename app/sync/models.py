"""Internal DTOs for planning and dispatch (not SQLAlchemy)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class TaskSpec:
    """Immutable plan unit persisted as ``sync_tasks``."""

    task_category: str
    task_type: str
    idempotency_key: str
    provider: str = "aws"
    scope_type: str = "cloud_account"
    scope_id: UUID | None = None
    parent_key: str | None = None
    priority: int = 100
    max_retries: int = 3
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class JobPlan:
    """Planner output: ordered task specs for a job."""

    task_specs: list[TaskSpec]
    critical_task_keys: frozenset[str] = frozenset()
    """If any of these idempotency keys fail, job should move to ``failed``."""
