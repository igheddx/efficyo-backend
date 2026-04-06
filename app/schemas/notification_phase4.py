"""Pydantic schemas for Phase 4 notification configuration."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ── Notification Policy ───────────────────────────────────────────────────────

_VALID_PRIORITIES = frozenset({"low", "medium", "high"})
_VALID_EVENT_TYPES = frozenset({"top_actions", "critical_alert", "approval_pending", "execution_failed"})
_VALID_DIGEST_MODES = frozenset({"instant", "digest"})


class NotificationPolicyPatch(BaseModel):
    min_priority: str | None = None
    enabled_event_types: list[str] | None = None
    throttle_window_minutes: int | None = Field(None, ge=1, le=1440)
    max_per_window: int | None = Field(None, ge=1, le=100)
    digest_mode: str | None = None

    @field_validator("min_priority")
    @classmethod
    def _check_priority(cls, v: str | None) -> str | None:
        if v is not None and v not in _VALID_PRIORITIES:
            raise ValueError(f"min_priority must be one of {sorted(_VALID_PRIORITIES)}")
        return v

    @field_validator("enabled_event_types")
    @classmethod
    def _check_event_types(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            bad = set(v) - _VALID_EVENT_TYPES
            if bad:
                raise ValueError(f"Unknown event types: {sorted(bad)}")
        return v

    @field_validator("digest_mode")
    @classmethod
    def _check_digest_mode(cls, v: str | None) -> str | None:
        if v is not None and v not in _VALID_DIGEST_MODES:
            raise ValueError(f"digest_mode must be one of {sorted(_VALID_DIGEST_MODES)}")
        return v


class NotificationPolicyRead(BaseModel):
    id: UUID
    organization_id: UUID
    min_priority: str
    enabled_event_types: list[str] | None
    throttle_window_minutes: int
    max_per_window: int
    digest_mode: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Notification Schedule ─────────────────────────────────────────────────────

_VALID_FREQUENCIES = frozenset({"daily", "weekly"})
_VALID_TIMEZONES = frozenset({
    "UTC", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
    "America/Sao_Paulo", "Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Moscow",
    "Asia/Dubai", "Asia/Kolkata", "Asia/Singapore", "Asia/Tokyo", "Australia/Sydney",
})


class NotificationScheduleUpsert(BaseModel):
    frequency: str = "daily"
    day_of_week: int | None = Field(None, ge=0, le=6)
    time_of_day: str = "09:00"
    timezone: str = "UTC"
    is_enabled: bool = True

    @field_validator("frequency")
    @classmethod
    def _check_frequency(cls, v: str) -> str:
        if v not in _VALID_FREQUENCIES:
            raise ValueError(f"frequency must be one of {sorted(_VALID_FREQUENCIES)}")
        return v

    @field_validator("time_of_day")
    @classmethod
    def _check_time(cls, v: str) -> str:
        parts = v.split(":")
        if len(parts) != 2:
            raise ValueError("time_of_day must be HH:MM")
        h, m = parts
        if not h.isdigit() or not m.isdigit():
            raise ValueError("time_of_day must be HH:MM")
        if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
            raise ValueError("time_of_day out of range")
        return v

    @field_validator("timezone")
    @classmethod
    def _check_tz(cls, v: str) -> str:
        if v not in _VALID_TIMEZONES:
            raise ValueError(f"Unsupported timezone. Supported: {sorted(_VALID_TIMEZONES)}")
        return v


class NotificationScheduleRead(BaseModel):
    id: UUID
    organization_id: UUID
    frequency: str
    day_of_week: int | None
    time_of_day: str
    timezone: str
    is_enabled: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Notification Snooze ───────────────────────────────────────────────────────

class NotificationSnoozeCreate(BaseModel):
    entity_key: str = Field(..., min_length=1, max_length=256)
    snooze_until: datetime
    reason: str | None = None
    user_id: UUID | None = None  # null = org-wide


class NotificationSnoozeRead(BaseModel):
    id: UUID
    organization_id: UUID
    user_id: UUID | None
    entity_key: str
    snooze_until: datetime
    reason: str | None
    created_by_user_id: UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Health ────────────────────────────────────────────────────────────────────

class NotificationHealthRead(BaseModel):
    organization_id: UUID
    last_delivery_at: datetime | None
    last_failure_at: datetime | None
    last_failure_error: str | None
    delivery_count_24h: int
    failure_count_24h: int
    pending_retries: int
    integrations: list[dict[str, Any]]
