"""Pydantic models for the AWS CloudFormation-based self-service onboarding flow."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.core.aws_field_validation import validate_role_arn

# ── Status literals ────────────────────────────────────────────────────────────

ReadOnlyStatus = Literal[
    "pending",
    "awaiting_role_arn",
    "validating",
    "connected",
    "failed",
]

ExecutionStatus = Literal[
    "not_configured",
    "awaiting_role_arn",
    "validating",
    "connected",
    "failed",
]

OnboardingMode = Literal["read_only", "read_and_execution"]

# ── Request schemas ────────────────────────────────────────────────────────────


class OnboardingStartRequest(BaseModel):
    """Initiate a new CloudFormation-based onboarding for an AWS account."""

    tenant_id: UUID
    name: str = Field(..., min_length=1, max_length=255, description="Customer-facing account display name")
    region_default: str = Field("us-east-1", min_length=1, max_length=64)
    onboarding_mode: OnboardingMode = Field(
        "read_and_execution",
        description="Whether to include execution role in the CloudFormation stack.",
    )
    notes: str | None = Field(default=None, max_length=2000)


class OnboardingConfirmRolesRequest(BaseModel):
    """Customer submits the ARNs output by their CloudFormation stack."""

    read_only_role_arn: str = Field(..., min_length=1, max_length=512)
    execution_role_arn: str | None = Field(default=None, max_length=512)

    @field_validator("read_only_role_arn", mode="before")
    @classmethod
    def _check_read_only_arn(cls, v: str) -> str:
        return validate_role_arn(str(v))

    @field_validator("execution_role_arn", mode="before")
    @classmethod
    def _check_exec_arn(cls, v: str | None) -> str | None:
        if v is None or v.strip() == "":
            return None
        return validate_role_arn(v.strip())


# ── Response schemas ───────────────────────────────────────────────────────────


class CfnLaunchParams(BaseModel):
    """Parameters pre-filled in the CloudFormation launch URL."""

    platform_aws_account_id: str
    external_id: str
    read_only_role_name: str
    execution_role_name: str
    include_execution_role: bool
    cfn_launch_url: str
    template_url: str


class OnboardingRead(BaseModel):
    """Status response for an onboarding session."""

    id: UUID
    tenant_id: UUID
    name: str
    region_default: str
    onboarding_mode: str | None
    read_only_status: str | None
    execution_status: str | None
    external_id: str | None
    role_arn: str | None  # blank until confirmed
    execution_role_arn: str | None
    connection_status: str
    cf_stack_launched_at: datetime | None
    last_validated_at: datetime | None
    last_validation_error: str | None
    onboarding_token: str | None
    cfn_launch: CfnLaunchParams | None = None

    model_config = {"from_attributes": True}


class OnboardingValidationResult(BaseModel):
    """Returned after AssumeRole validation runs."""

    read_only_validated: bool
    execution_validated: bool | None
    read_only_error: str | None = None
    execution_error: str | None = None
    aws_account_id: str | None = None


class OnboardingInviteRequest(BaseModel):
    """Admin sends an AWS connect invite to a customer email address."""

    tenant_id: UUID
    email: str = Field(..., min_length=3, max_length=320, description="Customer email to receive the connect link")
    account_name: str = Field("My AWS Account", min_length=1, max_length=255)
    region_default: str = Field("us-east-1", min_length=1, max_length=64)
    onboarding_mode: OnboardingMode = Field("read_and_execution")


class OnboardingInviteResponse(BaseModel):
    """Response after sending an invite — includes the generated connect URL."""

    session: OnboardingRead
    connect_url: str
    email_sent: bool
