from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class TenantCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    organization_id: UUID | None = Field(
        default=None,
        description="Platform administrators only: create the customer under this organization.",
    )


class TenantRead(BaseModel):
    id: UUID
    organization_id: UUID | None = None
    name: str
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
