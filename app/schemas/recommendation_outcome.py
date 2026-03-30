from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class SavingsProofSummaryRead(BaseModel):
    """Aggregated proof-of-savings (rolling 30d account UnblendedCost basis)."""

    outcomes_with_estimate_count: int = 0
    total_estimated_monthly_savings_proof: float = 0.0
    cost_window_label: str = "Rolling last 30 days"
    cost_metric: str = "UnblendedCost"


class RecommendationOutcomeCreate(BaseModel):
    notes: str | None = None


class RecommendationOutcomeUpdate(BaseModel):
    status: Literal["pending", "acted_on", "verified"] | None = None
    notes: str | None = None


class RecommendationApprovalUpdate(BaseModel):
    approved_by: str | None = None
    approval_comment: str | None = Field(default=None, max_length=4000)


class RecommendationAppliedUpdate(BaseModel):
    applied_by: str | None = None
    execution_notes: str | None = None


class RecommendationExecuteRequest(BaseModel):
    executed_by: str
    tag_values: dict[str, str] | None = None


class RecommendationExecuteRead(BaseModel):
    recommendation_id: UUID
    execution_status: Literal["success", "failed"]
    workflow_status: Literal["suggested", "approved", "applied", "verified", "rejected"]
    applied_by: str | None = None
    applied_role: str | None = None
    applied_at: datetime | None = None
    execution_notes: str | None = None
    rollback_guidance: str


class PreflightCheckRead(BaseModel):
    name: str
    status: Literal["pass", "warning", "fail"]
    message: str | None = None


class RecommendationPreflightRead(BaseModel):
    recommendation_id: UUID
    status: Literal["ready", "warning", "blocked"]
    risk_level: str
    safe_to_apply: bool
    checks: list[PreflightCheckRead]


class RecommendationDryRunRead(BaseModel):
    recommendation_id: UUID
    recommendation_type: str
    risk_level: str
    before: dict[str, Any]
    after: dict[str, Any]
    impact_summary: str


class RecommendationWorkflowRead(BaseModel):
    recommendation_id: UUID
    recommendation_summary: str | None = None
    recommendation_type: str
    workflow_status: Literal["suggested", "approved", "applied", "verified", "rejected"]
    approved_by: str | None = None
    approved_role: str | None = None
    approved_at: datetime | None = None
    applied_by: str | None = None
    applied_role: str | None = None
    applied_at: datetime | None = None
    execution_notes: str | None = None
    realized_savings: float | None = None


class RecommendationWorkflowTimelineRead(BaseModel):
    recommendation_id: UUID
    summary: str | None = None
    recommendation_category: str | None = None
    workflow_status: Literal["suggested", "approved", "applied", "verified", "rejected"]
    approved_by: str | None = None
    approved_role: str | None = None
    approved_at: datetime | None = None
    applied_by: str | None = None
    applied_role: str | None = None
    applied_at: datetime | None = None
    execution_notes: str | None = None
    impact_status: Literal["success", "no_change", "regression"] | None = None
    realized_savings: float | None = None
    last_evaluated_at: datetime | None = None
    before_cost: float | None = None
    after_cost: float | None = None
    estimated_savings: float | None = None
    savings_verified_at: datetime | None = None


class RecommendationWorkflowProgressRead(BaseModel):
    verified_recommendations_count: int
    applied_recommendations_count: int
    approved_recommendations_count: int
    realized_monthly_savings_total: float
    pending_verification_count: int


class RecommendationOutcomeRead(BaseModel):
    id: UUID
    tenant_id: UUID
    cloud_account_id: UUID
    recommendation_id: UUID
    resource_id: str
    recommendation_type: str
    recommendation_category: str
    status: Literal["pending", "acted_on", "verified"]
    acted_on_at: datetime | None
    baseline_monthly_cost: float | None
    current_monthly_cost: float | None
    estimated_savings_at_action: float | None
    realized_savings: float | None
    before_cost: float | None = None
    after_cost: float | None = None
    estimated_savings: float | None = None
    savings_verified_at: datetime | None = None
    impact_status: Literal["success", "no_change", "regression"] | None = None
    impact_summary: str | None = None
    follow_up_recommendation: str | None = None
    last_evaluated_at: datetime | None = None
    workflow_status: Literal["suggested", "approved", "applied", "verified", "rejected"] = "suggested"
    approved_by: str | None = None
    approved_role: str | None = None
    approved_at: datetime | None = None
    approval_comment: str | None = None
    rejection_reason: str | None = None
    rejected_at: datetime | None = None
    rejected_by: str | None = None
    applied_by: str | None = None
    applied_role: str | None = None
    applied_at: datetime | None = None
    execution_notes: str | None = None
    preflight_passed_at: datetime | None = None
    applied_via_auto: bool = False
    notes: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

