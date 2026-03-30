from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class NotificationRead(BaseModel):
    id: UUID
    type: str
    message: str
    entity_type: Optional[str] = None
    entity_id: Optional[UUID] = None
    payload: Optional[dict[str, Any]] = None
    is_read: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class NotificationsPageRead(BaseModel):
    items: list[NotificationRead]
    total: int
    unread_count: int


class MarkReadResponse(BaseModel):
    id: UUID
    is_read: bool


class MarkAllReadResponse(BaseModel):
    updated: int = Field(..., description="Number of notifications marked read.")
