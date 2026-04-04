from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.organization import OrgMembershipRead


class EligibleApproverRead(OrgMembershipRead):
    """Org member row plus operational role on the requested tenant/cloud account (approver or admin)."""

    effective_access_role: str = Field(
        ...,
        description="Effective operational access for this tenant/account (approver or admin for everyone listed).",
    )


class ApprovalRequestCreate(BaseModel):
    recommendation_id: UUID
    organization_id: UUID
    cloud_account_id: UUID
    approver_user_ids: list[UUID] = Field(..., min_length=1)
    execution_owner_user_id: UUID
    approval_mode: str = Field(default="all_required", max_length=32)
    tag_values: dict[str, str] | None = None


class ApprovalAssignmentRead(BaseModel):
    id: UUID
    approver_user_id: UUID
    approver_name_snapshot: str
    approver_role_snapshot: str
    status: str
    acted_at: Optional[datetime] = None
    comment: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ApprovalRequestRead(BaseModel):
    id: UUID
    organization_id: UUID
    tenant_id: UUID
    cloud_account_id: UUID
    recommendation_id: UUID
    recommendation_summary: Optional[str] = None
    submitted_by: Optional[str] = None
    submitted_by_role: Optional[str] = None
    approval_mode: str
    status: str
    submitted_at: datetime
    approved_at: Optional[datetime] = None
    rejected_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    approvals_complete: int = 0
    approvals_required: int = 0
    execution_owner_user_id: UUID | None = None
    execution_owner_name: str | None = None
    execution_owner_role: str | None = None
    tag_values: dict[str, str] | None = None
    tenant_name: Optional[str] = None
    cloud_account_name: Optional[str] = None
    recommendation_type: Optional[str] = None
    executed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ApprovalRequestDetailRead(ApprovalRequestRead):
    assignments: list[ApprovalAssignmentRead] = Field(default_factory=list)


class ApprovalDecisionBody(BaseModel):
    comment: Optional[str] = Field(default=None, max_length=4000)
