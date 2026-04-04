from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class GroupedItemResourceRead(BaseModel):
    recommendation_id: UUID | None = None
    finding_id: UUID | None = None
    resource_id: str
    resource_type: str
    summary: str | None = None
    severity: str | None = None
    risk_level: str | None = None
    workflow_status: str | None = None
    current_tags: dict[str, str] | None = None
    missing_required_tags: list[str] = Field(default_factory=list)


class GroupedItemRead(BaseModel):
    group_key: str
    total_count: int
    resource_type_breakdown: dict[str, int]
    severity_summary: dict[str, int] | None = None
    risk_summary: dict[str, int] | None = None
    workflow_summary: dict[str, int] | None = None
    impact_summary: dict[str, int] | None = None
    effort_summary: dict[str, int] | None = None
    confidence_summary: dict[str, int] | None = None
    actionability_summary: dict[str, int] | None = None
    priority_group_summary: dict[str, int] | None = None
    owner_summary: dict[str, int] | None = None
    related_recommendation_type: str | None = None
    guided_actions: list[str] = Field(default_factory=list)
    resources: list[GroupedItemResourceRead] = Field(default_factory=list)


class TaggingBatchResourceInput(BaseModel):
    recommendation_id: UUID
    proposed_tags: dict[str, str]


class TaggingBatchCreateRequest(BaseModel):
    recommendation_type: str
    title: str | None = None
    required_tag_keys: list[str] = Field(default_factory=list)
    shared_tag_values: dict[str, str] = Field(default_factory=dict)
    resources: list[TaggingBatchResourceInput] = Field(min_length=1)
    approver_user_ids: list[UUID] = Field(min_length=1)
    execution_owner_user_id: UUID
    notes: str | None = None


class TaggingBatchResourceRead(BaseModel):
    id: UUID
    recommendation_id: UUID
    resource_id: str
    resource_type: str
    current_tags_json: dict | None = None
    missing_required_tags_json: list[str] | None = None
    proposed_tags_json: dict
    execution_status: str
    execution_error: str | None = None
    executed_at: datetime | None = None

    model_config = {"from_attributes": True}


class TaggingBatchRead(BaseModel):
    id: UUID
    organization_id: UUID
    tenant_id: UUID
    cloud_account_id: UUID
    approval_request_id: UUID | None = None
    recommendation_type: str
    title: str
    status: str
    requested_by: str | None = None
    requested_by_role: str | None = None
    execution_notes: str | None = None
    required_tag_keys_json: list[str] | None = None
    shared_tags_json: dict | None = None
    summary_json: dict | None = None
    created_at: datetime
    updated_at: datetime
    resources: list[TaggingBatchResourceRead] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class TaggingBatchExecuteRequest(BaseModel):
    execution_notes: str | None = None


class TaggingBatchExecuteRead(BaseModel):
    batch_id: UUID
    status: str
    pending: int
    approved: int
    in_progress: int
    completed: int
    failed: int
    blocked: int
