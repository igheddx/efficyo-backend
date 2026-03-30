from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


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


class AccessGrantUpdate(BaseModel):
    access_role: str = Field(..., min_length=1, max_length=20)
