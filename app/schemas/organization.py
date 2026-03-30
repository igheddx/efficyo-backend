from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class OrganizationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)


class OrganizationRead(BaseModel):
    id: UUID
    name: str
    slug: str
    status: str = "active"
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class OrganizationDetailRead(OrganizationRead):
    """Single-org payload including lightweight counts for admin UI."""

    member_count: int = 0


class OrgMembershipRead(BaseModel):
    id: UUID
    organization_id: UUID
    user_id: UUID | None
    email: str
    display_name: str | None = None
    role: str
    created_at: datetime
    updated_at: datetime


class OrgMembershipCreate(BaseModel):
    """Add or update membership. Use `email` (preferred) or legacy `user_id` (same value: account email)."""

    role: str = Field(..., min_length=1, max_length=20)
    email: str | None = Field(None, max_length=320)
    user_id: str | None = Field(None, max_length=320, description="Deprecated; same as email.")
    password: str | None = Field(
        None,
        min_length=8,
        max_length=256,
        description="Required when the account does not exist yet (local user creation).",
    )
    display_name: str | None = Field(None, max_length=255)

    @model_validator(mode="after")
    def _require_login(self) -> "OrgMembershipCreate":
        if not (self.email or self.user_id or "").strip():
            raise ValueError("Provide email or user_id (the user's login email).")
        return self


class OrgMembershipUpdate(BaseModel):
    role: str = Field(..., min_length=1, max_length=20)
