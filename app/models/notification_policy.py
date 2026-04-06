"""Org-level notification policy: priority filter, event-type gating, throttle config."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy import JSON

from app.core.db import Base, utc_now


class NotificationPolicy(Base):
    """Organization-level notification policy.

    Controls:
    - min_priority      : drop events below this level ('low'|'medium'|'high')
    - enabled_event_types: JSON list of allowed event types; null = all allowed
    - throttle_window_minutes: dedup window length (replaces hard-coded 60s)
    - max_per_window    : max deliveries per (provider, target) per window
    - digest_mode       : 'instant' or 'digest' (digest = batch via schedule)
    """

    __tablename__ = "notification_policies"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_notification_policy_org"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Priority filter — events below this threshold are silently dropped.
    min_priority = Column(String(16), nullable=False, default="low")

    # Allowlist of event types (e.g. ["top_actions", "critical_alert"]). null = all.
    enabled_event_types = Column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )

    # Rate-limiting / throttle
    throttle_window_minutes = Column(Integer, nullable=False, default=60)
    max_per_window = Column(Integer, nullable=False, default=10)

    # Delivery mode: 'instant' sends immediately, 'digest' batches via schedule
    digest_mode = Column(String(16), nullable=False, default="instant")

    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
