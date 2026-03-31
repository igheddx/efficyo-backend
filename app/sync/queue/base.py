"""Queue protocol: enqueue, dequeue, retry scheduling, event fan-out."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy.orm import Session


@runtime_checkable
class TaskQueue(Protocol):
    """Pluggable backend (database poll, SQS, Redis Streams, …)."""

    def enqueue_task(self, db: Session, *, task_id: UUID, task_type: str, payload: dict[str, Any]) -> None:
        """Notify workers that a task is ready (no-op when tasks are already ``queued`` rows)."""
        ...

    def dequeue_task(self, db: Session, *, worker_id: str) -> UUID | None:
        """Return claimed task id, or None if queue empty."""
        ...

    def schedule_retry(
        self,
        db: Session,
        *,
        task_id: UUID,
        delay_seconds: int = 0,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        ...

    def publish_event(
        self,
        db: Session,
        *,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Optional outbox / bus hook (default: no-op)."""
        ...


class NullQueue:
    """Testing / inert implementation."""

    def enqueue_task(self, db: Session, *, task_id: UUID, task_type: str, payload: dict[str, Any]) -> None:
        return None

    def dequeue_task(self, db: Session, *, worker_id: str) -> UUID | None:
        return None

    def schedule_retry(
        self,
        db: Session,
        *,
        task_id: UUID,
        delay_seconds: int = 0,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        return None

    def publish_event(self, db: Session, *, event_type: str, payload: dict[str, Any]) -> None:
        return None
