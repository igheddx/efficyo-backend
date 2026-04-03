from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.core.password_policy import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH


class RootMembershipBrief(BaseModel):
    organization_id: UUID
    organization_name: str
    role: str
    created_at: datetime


class RootGlobalUserRow(BaseModel):
    """One row per org membership (user may appear multiple times)."""

    membership_id: UUID
    user_id: UUID | None
    email: str
    display_name: str | None
    role: str
    user_status: str
    organization_id: UUID
    organization_name: str
    last_login_at: datetime | None
    created_at: datetime


class RootUserCreate(BaseModel):
    organization_id: UUID
    email: str = Field(..., min_length=3, max_length=320)
    password: str | None = Field(
        None,
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        description="Deprecated: ignored for new users.",
    )
    display_name: str | None = Field(None, max_length=255)
    role: str = Field(..., description="org_admin, approver, or viewer")

    @field_validator("role")
    @classmethod
    def _role(cls, v: str) -> str:
        r = v.strip().lower()
        if r not in {"org_admin", "approver", "viewer"}:
            raise ValueError("role must be org_admin, approver, or viewer")
        return r


class RootUserDetail(BaseModel):
    id: UUID
    email: str
    display_name: str
    status: str
    is_root_admin: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime
    memberships: list[RootMembershipBrief]


class RootUserStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(active|pending|disabled)$")
