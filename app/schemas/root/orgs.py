from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class RootOrganizationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: str | None = Field(None, max_length=255, description="Optional; must be unique if provided.")


class RootOrganizationStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(active|disabled)$")


class RootOrganizationListItem(BaseModel):
    id: UUID
    name: str
    slug: str
    status: str
    user_count: int = 0
    aws_account_count: int = 0
    last_scan_at: datetime | None = None
    pending_approvals: int = 0
    created_at: datetime
    updated_at: datetime


class RootOrganizationDetail(RootOrganizationListItem):
    """Same shape as list row; reserved for future extra fields."""
