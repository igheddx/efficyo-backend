from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.aws_field_validation import (
    account_id_from_role_arn,
    normalize_aws_account_id,
    validate_aws_account_id,
    validate_region_default,
    validate_role_arn,
)
from app.schemas.ingestion_job import IngestionJobRead


class CloudAccountCreate(BaseModel):
    account_id: str = Field(..., min_length=1, max_length=32)
    name: str = Field(..., min_length=1, max_length=255)
    role_arn: str = Field(..., min_length=1, max_length=512)
    region_default: str = Field(..., min_length=1, max_length=64)
    trigger_initial_sync: bool = False

    @field_validator("account_id", mode="before")
    @classmethod
    def _coerce_account_id(cls, v: str) -> str:
        if v is None:
            raise ValueError("account_id is required")
        return validate_aws_account_id(str(v))

    @field_validator("role_arn", mode="before")
    @classmethod
    def _coerce_role_arn(cls, v: str) -> str:
        if v is None:
            raise ValueError("role_arn is required")
        return validate_role_arn(str(v))

    @field_validator("region_default", mode="before")
    @classmethod
    def _coerce_region(cls, v: str) -> str:
        if v is None:
            raise ValueError("region_default is required")
        return validate_region_default(str(v))

    @model_validator(mode="after")
    def _account_matches_arn(self):
        aid_arn = account_id_from_role_arn(self.role_arn)
        if aid_arn and aid_arn != normalize_aws_account_id(self.account_id):
            raise ValueError("AWS account ID must match the account in the role ARN.")
        return self


class CloudAccountTestConnectionRequest(BaseModel):
    account_id: str = Field(..., min_length=1, max_length=32)
    role_arn: str = Field(..., min_length=1, max_length=512)
    region_default: str = Field(..., min_length=1, max_length=64)

    @field_validator("account_id", mode="before")
    @classmethod
    def _v_account(cls, v: str) -> str:
        return validate_aws_account_id(str(v) if v is not None else "")

    @field_validator("role_arn", mode="before")
    @classmethod
    def _v_role(cls, v: str) -> str:
        return validate_role_arn(str(v) if v is not None else "")

    @field_validator("region_default", mode="before")
    @classmethod
    def _v_region(cls, v: str) -> str:
        return validate_region_default(str(v) if v is not None else "")

    @model_validator(mode="after")
    def _match_arn(self):
        aid_arn = account_id_from_role_arn(self.role_arn)
        if aid_arn and aid_arn != normalize_aws_account_id(self.account_id):
            raise ValueError("AWS account ID must match the account in the role ARN.")
        return self


class CloudAccountTestConnectionResponse(BaseModel):
    success: bool
    error_message: Optional[str] = None
    aws_account_id: Optional[str] = None
    arn: Optional[str] = None
    user_id: Optional[str] = None


class CloudAccountRead(BaseModel):
    id: UUID
    tenant_id: UUID
    account_id: str
    name: str
    status: str
    connection_status: str = "untested"
    last_validated_at: Optional[datetime] = None
    last_validation_error: Optional[str] = None
    role_arn: str
    region_default: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CloudAccountProvisionedRead(CloudAccountRead):
    """Returned from POST create when optional initial sync is requested."""

    initial_sync_job: Optional[IngestionJobRead] = None


class CloudAccountValidationRead(BaseModel):
    cloud_account_id: UUID
    status: str
    success: bool
    aws_account_id: Optional[str] = None
    arn: Optional[str] = None
    user_id: Optional[str] = None
    error_message: Optional[str] = None


class ResourceIngestionRead(BaseModel):
    cloud_account_id: UUID
    resource_type: str
    ingested_count: int
    captured_at: datetime


class FindingRead(BaseModel):
    id: UUID
    resource_id: str
    resource_type: str
    finding_type: str
    severity: str
    evidence_json: dict
    estimated_savings: float | None = None
    detected_at: datetime
    sync_run_id: UUID | None = None

    model_config = {"from_attributes": True}


class DetectionRunRead(BaseModel):
    cloud_account_id: UUID
    resource_type: str
    findings_created: int
    detected_at: datetime
    sync_run_id: UUID


class RecommendationRead(BaseModel):
    id: UUID
    resource_id: str
    resource_type: str
    recommendation_type: str
    recommendation_category: str
    summary: str
    ai_explanation: str | None = None
    explanation: str
    risk_level: str
    confidence_score: str
    recommended_action: str
    estimated_savings: float | None = None
    savings_basis: str | None = None
    confidence_reason: str | None = None
    why_it_matters: str | None = None
    learned_confidence: str | None = None
    learned_confidence_reason: str | None = None
    historical_success_rate: float | None = None
    avg_realized_savings_for_type: float | None = None
    steps: list[str] = []
    estimated_time: str | None = None
    difficulty: str | None = None
    workflow_status: str | None = None
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
    evidence_json: dict | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class RecommendationRunRead(BaseModel):
    cloud_account_id: UUID
    recommendations_created: int
    created_at: datetime


class SummaryRecommendationRead(BaseModel):
    recommendation_id: UUID
    resource_id: str
    recommendation_type: str
    recommendation_category: str
    summary: str
    estimated_savings: float | None = None
    risk_level: str
    confidence_score: str
    learned_confidence: str | None = None
    learned_confidence_reason: str | None = None
    historical_success_rate: float | None = None
    avg_realized_savings_for_type: float | None = None


class TopCostServiceRead(BaseModel):
    service: str
    amount: float


class CostTrendRead(BaseModel):
    service: str
    trend: str
    percent_change: float
    current_cost: float
    previous_cost: float
    summary: str


class CostTrendsListRead(BaseModel):
    cost_window: str
    cost_window_label: str
    cost_metric: str = "UnblendedCost"
    trends: list[CostTrendRead]


class CostTrendPointRead(BaseModel):
    date: str
    total_cost: float


class CostTrendsSeriesRead(BaseModel):
    days: int
    points: list[CostTrendPointRead]
    cost_window: str = ""
    cost_window_label: str = ""
    cost_metric: str = ""


class SavingsTrendPointRead(BaseModel):
    date: str
    savings_realized: float


class RecommendationBeforeAfterRead(BaseModel):
    recommendation_id: UUID
    summary: str | None = None
    before_cost: float | None = None
    after_cost: float | None = None
    savings: float | None = None


class SavingsTrendsSeriesRead(BaseModel):
    days: int
    points: list[SavingsTrendPointRead]
    before_after: list[RecommendationBeforeAfterRead]
    cost_window: str = ""
    cost_window_label: str = ""
    cost_metric: str = ""


class SummaryRead(BaseModel):
    cloud_account_id: UUID
    total_estimated_monthly_savings: float
    total_cost: float
    savings_percentage: float
    total_recommendations: int
    by_category: dict[str, int]
    by_severity: dict[str, int]
    top_cost_services: list[TopCostServiceRead]
    top_savings_opportunity: SummaryRecommendationRead | None = None
    top_risk_issue: SummaryRecommendationRead | None = None
    cost_period_start: str = ""
    cost_period_end: str = ""
    cost_window: str = "rolling_30d"
    cost_window_label: str = "Rolling last 30 days"
    cost_metric: str = "UnblendedCost"


class CostByServiceRead(BaseModel):
    service: str
    amount: float


class CostSummaryRead(BaseModel):
    start_date: str
    end_date: str
    total_cost: float
    by_service: list[CostByServiceRead]
    cost_window: str = "rolling_30d"
    cost_window_label: str = "Rolling last 30 days"
    cost_metric: str = "UnblendedCost"


class Ec2OtherBreakdownItemRead(BaseModel):
    category: str
    amount: float


class Ec2OtherBreakdownRead(BaseModel):
    ec2_other_total: float
    breakdown: list[Ec2OtherBreakdownItemRead]
    cost_window: str = "rolling_30d"
    cost_window_label: str = "Rolling last 30 days"
    cost_metric: str = "UnblendedCost"


class SimulationRead(BaseModel):
    recommendation_id: UUID
    resource_id: str
    recommendation_type: str
    recommendation_category: str
    current_state: dict
    proposed_state: dict
    impact_summary: str
    risk_reduction: str
    estimated_savings: float | None = None
    confidence_score: str


class TopOpportunityRead(BaseModel):
    recommendation_id: UUID
    resource_id: str
    resource_type: str
    recommendation_type: str
    recommendation_category: str
    summary: str
    ai_explanation: str | None = None
    estimated_savings: float | None = None
    risk_level: str
    confidence_score: str
    computed_score: float
    normalized_savings: float
    risk_factor: float
    confidence_factor: float
    urgency_factor: float
    ranking_reason: str
    priority_bucket: str
    savings_basis: str | None = None
    confidence_reason: str | None = None
    why_it_matters: str | None = None
    learned_confidence: str | None = None
    learned_confidence_reason: str | None = None
    historical_success_rate: float | None = None
    avg_realized_savings_for_type: float | None = None
    steps: list[str] = []
    estimated_time: str | None = None
    difficulty: str | None = None


class InsightsRead(BaseModel):
    summary_text: str
    cost_basis_note: str = ""
    cost_window: str = "rolling_30d"
    cost_window_label: str = "Rolling last 30 days"
    cost_metric: str = "UnblendedCost"


class ActionPlanItemRead(BaseModel):
    step_number: int
    recommendation_id: UUID
    recommendation_type: str
    resource_id: str
    resource_type: str
    summary: str
    estimated_savings: float | None = None
    risk_level: str
    reason: str
    expected_impact: str


class ActionPlanRead(BaseModel):
    cost_window: str = "rolling_30d"
    cost_window_label: str = "Rolling last 30 days"
    cost_metric: str = "UnblendedCost"
    items: list[ActionPlanItemRead]


class RecommendationGuideRead(BaseModel):
    steps: list[str]
    estimated_time: str
    difficulty: str


class ExecutionPlanRead(BaseModel):
    cli_command: str
    terraform_snippet: str | None = None
    notes: str
    risk: str
    rollback: str