from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class CostFreshnessRead(BaseModel):
    tenant_id: UUID
    cloud_account_id: UUID
    is_snapshot_missing: bool
    is_stale: bool
    last_updated_at: str | None = None
    stale_after_minutes: int | None = None


class CostUsageItemRead(BaseModel):
    feature_name: str
    request_type: str
    api_name: str
    was_cache_hit: bool
    created_at: datetime


class CostUsageRead(BaseModel):
    items: list[CostUsageItemRead]
    total: int


class CostUsageSummaryRead(BaseModel):
    start_at: str | None = None
    end_at: str | None = None
    total_calls: int
    live_calls: int
    cache_hits: int
    estimated_call_cost_total: float


class CostUsageGroupItemRead(BaseModel):
    group_key: str
    total_calls: int
    live_calls: int
    cache_hits: int
    estimated_call_cost_total: float


class CostUsageGroupRead(BaseModel):
    start_at: str | None = None
    end_at: str | None = None
    items: list[CostUsageGroupItemRead]

