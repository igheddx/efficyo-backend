from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy import JSON

from app.core.db import Base, utc_now


class NotificationDeliveryLog(Base):
    """Trace table for outbound notification delivery attempts and routing outcomes."""

    __tablename__ = "notification_delivery_logs"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "target_type",
            "target_key",
            "dedupe_key",
            name="uq_notification_delivery_provider_target_dedupe",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    notification_id = Column(
        UUID(as_uuid=True),
        ForeignKey("notifications.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    event_type = Column(String(64), nullable=False, index=True)
    provider = Column(String(32), nullable=False, index=True)
    target_type = Column(String(16), nullable=False)  # org | user
    target_key = Column(String(256), nullable=False)  # org_id or user_id/chat/identifier

    route_kind = Column(String(32), nullable=False)  # org_channel | direct_user | fallback_org
    status = Column(String(32), nullable=False)  # delivered | failed | skipped_dedupe | rate_limited
    error = Column(Text, nullable=True)

    dedupe_key = Column(String(200), nullable=False, index=True)
    rate_limited = Column(Boolean, nullable=False, default=False)

    # Retry tracking (Phase 4)
    retry_count = Column(Integer, nullable=False, default=0)
    next_retry_at = Column(DateTime(timezone=True), nullable=True, index=True)
    # Raw provider response (truncated) or error body for debugging / retry (Phase 4)
    provider_response = Column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )

    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
