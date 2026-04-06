"""Notification snooze / suppression records."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.core.db import Base, utc_now


class NotificationSnooze(Base):
    """Suppress a specific event type (or item) until snooze_until.

    entity_key is a free-form string identifying what is snoozed:
      - event_type only:        "top_actions"
      - event + resource fingerprint: "critical_alert:finding:<finding_id>"

    Scope:
      user_id = null → org-wide snooze (affects all channels)
      user_id set   → per-user snooze (only affects that user's personal routing)
    """

    __tablename__ = "notification_snoozes"
    __table_args__ = (
        Index("ix_notification_snooze_lookup", "organization_id", "entity_key", "snooze_until"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    entity_key = Column(String(256), nullable=False, index=True)
    snooze_until = Column(DateTime(timezone=True), nullable=False, index=True)
    reason = Column(Text, nullable=True)
    created_by_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
