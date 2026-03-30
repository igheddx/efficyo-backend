from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class PolicyProfileRead(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    config_json: dict
    created_at: datetime

    model_config = {"from_attributes": True}
