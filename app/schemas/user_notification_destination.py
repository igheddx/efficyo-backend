from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class UserNotificationDestinationRead(BaseModel):
    id: UUID
    user_id: UUID
    organization_id: UUID

    slack_user_id: str | None = None
    teams_user_identifier: str | None = None
    telegram_chat_id: str | None = None

    notifications_enabled: bool
    receive_direct_notifications: bool
    receive_approvals: bool
    receive_failures: bool

    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None

    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UserNotificationDestinationPatch(BaseModel):
    organization_id: UUID

    slack_user_id: str | None = Field(default=None, max_length=128)
    teams_user_identifier: str | None = Field(default=None, max_length=256)
    telegram_chat_id: str | None = Field(default=None, max_length=64)

    notifications_enabled: bool | None = None
    receive_direct_notifications: bool | None = None
    receive_approvals: bool | None = None
    receive_failures: bool | None = None

    quiet_hours_start: str | None = Field(default=None, max_length=5)
    quiet_hours_end: str | None = Field(default=None, max_length=5)

    @field_validator("quiet_hours_start", "quiet_hours_end")
    @classmethod
    def _validate_time(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        parts = v.split(":")
        if len(parts) != 2:
            raise ValueError("Quiet hours must be HH:MM format.")
        hh, mm = parts
        if not (hh.isdigit() and mm.isdigit()):
            raise ValueError("Quiet hours must be HH:MM format.")
        h = int(hh)
        m = int(mm)
        if h < 0 or h > 23 or m < 0 or m > 59:
            raise ValueError("Quiet hours must be HH:MM format.")
        return f"{h:02d}:{m:02d}"
