from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


SyncProvider = Literal["aws"]

SyncJobStatus = Literal[
    "queued",
    "planning",
    "collecting",
    "analyzing",
    "scoring",
    "summarizing",
    "completed",
    "completed_with_errors",
    "failed",
    "cancelled",
]

SyncTaskStatus = Literal["queued", "running", "succeeded", "failed", "retrying", "blocked", "skipped", "cancelled"]


class SyncJobCreate(BaseModel):
    provider: SyncProvider = "aws"
    trigger_type: str = "manual"
    tenant_id: UUID
    cloud_account_id: UUID
    force_new: bool = False


class SyncJobRead(BaseModel):
    id: UUID
    organization_id: UUID
    tenant_id: UUID
    cloud_account_id: UUID
    provider: SyncProvider
    initiated_by_user_id: UUID | None = None
    trigger_type: str
    status: SyncJobStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failed_at: datetime | None = None
    last_heartbeat_at: datetime | None = None

    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    skipped_tasks: int = 0

    summary_json: dict[str, Any] | None = None
    error_summary: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SyncTaskRead(BaseModel):
    id: UUID
    sync_job_id: UUID
    parent_task_id: UUID | None
    task_category: str
    task_type: str
    provider: SyncProvider
    scope_type: str
    scope_id: UUID | None
    idempotency_key: str
    status: SyncTaskStatus
    priority: int
    retry_count: int
    max_retries: int
    scheduled_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    failed_at: datetime | None
    last_heartbeat_at: datetime | None

    payload_json: dict[str, Any] | None
    result_json: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    worker_id: str | None

    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SyncJobEventRead(BaseModel):
    id: UUID
    sync_job_id: UUID
    sync_task_id: UUID | None
    event_type: str
    level: str
    message: str
    details_json: dict[str, Any] | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class RetrySyncJobRequest(BaseModel):
    delay_seconds: int = 0
    task_ids: list[UUID] = Field(default_factory=list)


class RetrySyncTaskRequest(BaseModel):
    delay_seconds: int = 0

