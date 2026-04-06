from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.core.db import Base, utc_now


class UserNotificationDestination(Base):
    """Per-user notification destination mapping and preferences per organization."""

    __tablename__ = "user_notification_destinations"
    __table_args__ = (
        UniqueConstraint("user_id", "organization_id", name="uq_user_notification_dest_user_org"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Provider identity mappings (optional)
    slack_user_id = Column(String(128), nullable=True)
    teams_user_identifier = Column(String(256), nullable=True)
    telegram_chat_id = Column(String(64), nullable=True)

    # Global + per-event user preferences
    notifications_enabled = Column(Boolean, nullable=False, default=True)
    receive_direct_notifications = Column(Boolean, nullable=False, default=True)
    receive_approvals = Column(Boolean, nullable=False, default=True)
    receive_failures = Column(Boolean, nullable=False, default=True)

    # Quiet hours in HH:MM 24h format (interpreted in UTC for Phase 3)
    quiet_hours_start = Column(String(5), nullable=True)
    quiet_hours_end = Column(String(5), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
