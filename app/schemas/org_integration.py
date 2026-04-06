"""Pydantic schemas for org-level integration config (Phase 2: Slack, Teams, Telegram)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

_SLACK_WEBHOOK_RE = re.compile(r"^https://hooks\.slack\.com/", re.IGNORECASE)
_HTTPS_RE = re.compile(r"^https://", re.IGNORECASE)


def _require_slack_webhook(v):
    if v is None or str(v).strip() == "":
        return None
    url = str(v).strip()
    if not _SLACK_WEBHOOK_RE.match(url):
        raise ValueError("Slack webhook URL must start with https://hooks.slack.com/")
    if len(url) > 512:
        raise ValueError("Webhook URL too long.")
    return url


def _require_https_url(v):
    if v is None or str(v).strip() == "":
        return None
    url = str(v).strip()
    if not _HTTPS_RE.match(url):
        raise ValueError("Webhook URL must use HTTPS.")
    if len(url) > 512:
        raise ValueError("Webhook URL too long.")
    return url


class SlackIntegrationUpsert(BaseModel):
    is_enabled: bool = True
    webhook_url: str | None = Field(default=None, max_length=512)
    channel_name: str | None = Field(default=None, max_length=128)

    @field_validator("webhook_url", mode="before")
    @classmethod
    def _check_webhook(cls, v):
        return _require_slack_webhook(v)


class TeamsIntegrationUpsert(BaseModel):
    is_enabled: bool = True
    webhook_url: str | None = Field(default=None, max_length=512)
    channel_name: str | None = Field(default=None, max_length=128)

    @field_validator("webhook_url", mode="before")
    @classmethod
    def _check_webhook(cls, v):
        return _require_https_url(v)


class TelegramIntegrationUpsert(BaseModel):
    is_enabled: bool = True
    bot_token: str | None = Field(default=None, max_length=256)
    chat_id: str | None = Field(default=None, max_length=64)
    channel_name: str | None = Field(default=None, max_length=128)


class OrgIntegrationUpsert(BaseModel):
    """Slack-focused upsert schema kept for Phase 1 backward-compat."""

    provider: Literal["slack"] = "slack"
    is_enabled: bool = True
    webhook_url: str | None = Field(default=None, max_length=512)
    channel_name: str | None = Field(default=None, max_length=128)

    @field_validator("webhook_url", mode="before")
    @classmethod
    def _check_webhook(cls, v):
        return _require_slack_webhook(v)


class OrgIntegrationRead(BaseModel):
    """Response schema - sensitive fields are masked."""

    id: UUID
    organization_id: UUID
    provider: str
    is_enabled: bool
    webhook_url_masked: str | None
    channel_name: str | None
    bot_token_masked: str | None = None
    chat_id: str | None = None
    last_test_sent_at: datetime | None
    last_digest_sent_at: datetime | None
    last_delivery_status: str | None
    last_delivery_error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class IntegrationActionResult(BaseModel):
    """Result of a test or digest send operation."""

    success: bool
    message: str
    provider: str
    last_delivery_status: str | None = None
    last_delivery_error: str | None = None


SlackActionResult = IntegrationActionResult


class DigestItem(BaseModel):
    title: str
    count: int | None = None
    impact: str | None = None
    reason: str | None = None
    account_name: str | None = None
    estimated_savings: float | None = None
    link: str | None = None
