"""Scheduled digest configuration for an organization."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.core.db import Base, utc_now


class NotificationSchedule(Base):
    """When to send the automatic top-actions digest for an org.

    frequency = 'daily'  → fires every day at time_of_day (UTC).
    frequency = 'weekly' → fires every day_of_week (0=Mon…6=Sun) at time_of_day.

    The worker sets last_run_at after each send and recomputes next_run_at.
    """

    __tablename__ = "notification_schedules"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_notification_schedule_org"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    frequency = Column(String(16), nullable=False, default="daily")   # 'daily' | 'weekly'
    day_of_week = Column(Integer, nullable=True)                       # 0=Mon … 6=Sun (weekly only)
    time_of_day = Column(String(5), nullable=False, default="09:00")   # HH:MM UTC
    timezone = Column(String(64), nullable=False, default="UTC")

    is_enabled = Column(Boolean, nullable=False, default=True)

    last_run_at = Column(DateTime(timezone=True), nullable=True)
    next_run_at = Column(DateTime(timezone=True), nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
