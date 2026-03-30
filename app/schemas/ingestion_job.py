from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class IngestionJobCreate(BaseModel):
    job_type: Literal["full_sync", "cost_refresh", "analysis_refresh"] = "full_sync"


class IngestionJobRead(BaseModel):
    id: UUID
    tenant_id: UUID
    cloud_account_id: UUID
    job_type: Literal["full_sync", "cost_refresh", "analysis_refresh"]
    status: Literal["queued", "running", "completed", "failed"]
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

