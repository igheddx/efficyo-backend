from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class AccessGrantRead(BaseModel):
    id: UUID
    user_id: UUID
    organization_id: UUID
    tenant_id: UUID
    cloud_account_id: UUID | None
    access_role: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AccessGrantCreate(BaseModel):
    user_id: UUID
    tenant_id: UUID
    cloud_account_id: UUID | None = Field(
        None, description="Null = all cloud accounts in the tenant; set for a single account override."
    )
    access_role: str = Field(..., min_length=1, max_length=20)

    @field_validator("cloud_account_id", mode="before")
    @classmethod
    def _blank_cloud_account_id(cls, v: Any) -> Any:
        """Empty string from clients must not reach UUID parsing (would yield 422)."""
        if v is None:
            return None
        if isinstance(v, str) and not v.strip():
            return None
        return v


class AccessGrantUpdate(BaseModel):
    access_role: str = Field(..., min_length=1, max_length=20)
