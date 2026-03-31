from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class CostRequestContext:
    org_id: UUID
    tenant_id: UUID
    cloud_account_id: UUID
    provider: str = "aws"
    feature_name: str = "unknown"
    request_type: str = "summary"
    sync_job_id: UUID | None = None


@dataclass(frozen=True)
class CostSnapshotReadModel:
    id: UUID
    org_id: UUID
    tenant_id: UUID
    cloud_account_id: UUID
    provider: str
    snapshot_date: date
    period_start: date
    period_end: date
    granularity: str
    total_cost: float
    currency: str
    service_breakdown: list[dict[str, Any]]
    daily_costs: list[dict[str, Any]]
    cost_trends: list[dict[str, Any]]
    ec2_other_breakdown: list[dict[str, Any]]
    waf_monthly_cost: float
    freshness_status: str
    stale_after_minutes: int
    updated_at: datetime
