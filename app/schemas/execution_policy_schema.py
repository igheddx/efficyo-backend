from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ExecutionPolicyRead(BaseModel):
    id: UUID
    organization_id: UUID | None
    tenant_id: UUID | None
    cloud_account_id: UUID | None
    recommendation_type: str
    risk_class: str
    execution_mode: str
    requires_all_approvals: bool
    preflight_required: bool
    rollback_required: bool
    is_enabled: bool
    created_at: datetime
    updated_at: datetime
    updated_by_email: str | None = None

    model_config = {"from_attributes": True}


class ExecutionPolicyCreate(BaseModel):
    organization_id: UUID | None = None
    tenant_id: UUID | None = None
    cloud_account_id: UUID | None = None
    recommendation_type: str = Field(..., min_length=1, max_length=100)
    risk_class: str = Field(default="any", max_length=20)
    execution_mode: Literal["manual_only", "approved_then_manual", "approved_then_auto_allowed"]
    requires_all_approvals: bool = True
    preflight_required: bool = False
    rollback_required: bool = True
    is_enabled: bool = True


class ExecutionPolicyPatch(BaseModel):
    risk_class: str | None = Field(default=None, max_length=20)
    execution_mode: Literal["manual_only", "approved_then_manual", "approved_then_auto_allowed"] | None = None
    requires_all_approvals: bool | None = None
    preflight_required: bool | None = None
    rollback_required: bool | None = None
    is_enabled: bool | None = None


class ExecutionEligibilityRead(BaseModel):
    execution_eligible: bool
    auto_execution_eligible: bool
    blocking_reason: str | None = None
    effective_execution_mode: str
    policy_id: str | None = None
    policy_scope_level: str | None = None
    display_status: str
    display_label: str
    preflight_required: bool = False
    preflight_passed: bool = False
