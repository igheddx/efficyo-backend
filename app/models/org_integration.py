"""Organization-level notification integration model.

Phase 1: Slack Incoming Webhook.
Later phases may add Teams, PagerDuty, etc. by inserting rows with different `provider` values.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.db import Base, utc_now


class OrgIntegration(Base):
    """Stores a notification channel config (e.g., Slack webhook) scoped to one organization.

    Uniqueness: one row per (organization_id, provider). Use UPSERT-style logic in the service.
    """

    __tablename__ = "org_integrations"
    __table_args__ = (
        UniqueConstraint("organization_id", "provider", name="uq_org_integrations_org_provider"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider = Column(String(32), nullable=False)          # "slack"
    is_enabled = Column(Boolean, nullable=False, default=True)
    # Incoming Webhook URL — Slack and Teams use this. Stored as plain text; never logged.
    webhook_url = Column(Text, nullable=True)
    # Human-readable display name for the target channel (display only, not used for delivery).
    channel_name = Column(String(128), nullable=True)
    # Telegram-specific config — bot token is never returned in full via the API.
    bot_token = Column(Text, nullable=True)
    chat_id = Column(String(64), nullable=True)
    # Delivery metadata
    last_test_sent_at = Column(DateTime(timezone=True), nullable=True)
    last_digest_sent_at = Column(DateTime(timezone=True), nullable=True)
    last_delivery_status = Column(String(16), nullable=True)   # "ok" | "error" | None
    last_delivery_error = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    # Relationship (lazy load is fine — integration is rarely queried with org)
    organization = relationship("Organization", lazy="select")

    def masked_webhook_url(self) -> str | None:
        """Return a masked version of the webhook URL safe for API responses."""
        if not self.webhook_url:
            return None
        return "****" + self.webhook_url[-6:]

    def masked_bot_token(self) -> str | None:
        """Return a masked bot token safe for API responses."""
        if not self.bot_token:
            return None
        return "****" + self.bot_token[-4:]
