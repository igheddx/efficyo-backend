from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class PendingApprovalItemRead(BaseModel):
    """One recommendation awaiting approver action (suggested / no outcome yet)."""

    recommendation_id: UUID
    tenant_id: UUID
    tenant_name: str
    cloud_account_id: UUID
    cloud_account_name: str
    cloud_account_aws_id: Optional[str] = None
    organization_id: UUID
    organization_name: str
    summary: str
    recommendation_category: str
    recommendation_type: str
    resource_id: str
    resource_type: str
    estimated_savings: float | None = None
    risk_level: str
    why_it_matters: str | None = None
    workflow_status: str = "suggested"
    preflight_status: str | None = Field(
        default=None,
        description="ready | warning | blocked | unavailable (only when preview was computed)",
    )
    dry_run_impact_summary: str | None = None
    preflight_checks: list[dict[str, Any]] | None = None


class PendingApprovalsPageRead(BaseModel):
    items: list[PendingApprovalItemRead]
    total: int


class RecommendationRejectRequest(BaseModel):
    rejection_reason: str = Field(..., min_length=1, max_length=4000)
