"""Durable sync pipeline: jobs, tasks, and audit events (orchestrator + workers)."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.db import Base, utc_now


class SyncJob(Base):
    __tablename__ = "sync_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    cloud_account_id = Column(
        UUID(as_uuid=True), ForeignKey("cloud_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    provider = Column(String(32), nullable=False, default="aws", index=True)
    initiated_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    trigger_type = Column(String(32), nullable=False, default="manual")
    status = Column(String(32), nullable=False, default="queued", index=True)

    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    failed_at = Column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at = Column(DateTime(timezone=True), nullable=True)

    total_tasks = Column(Integer, nullable=False, default=0)
    completed_tasks = Column(Integer, nullable=False, default=0)
    failed_tasks = Column(Integer, nullable=False, default=0)
    skipped_tasks = Column(Integer, nullable=False, default=0)

    summary_json = Column(JSONB().with_variant(JSON(), "sqlite"), nullable=True)
    error_summary = Column(Text, nullable=True)
    force_new = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    def __repr__(self) -> str:
        return f"<SyncJob(id={self.id}, status={self.status}, provider={self.provider})>"


class SyncTask(Base):
    __tablename__ = "sync_tasks"
    __table_args__ = (UniqueConstraint("sync_job_id", "idempotency_key", name="uq_sync_tasks_job_idempotency"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    sync_job_id = Column(UUID(as_uuid=True), ForeignKey("sync_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_task_id = Column(UUID(as_uuid=True), ForeignKey("sync_tasks.id", ondelete="SET NULL"), nullable=True)

    task_category = Column(String(24), nullable=False, index=True)
    task_type = Column(String(128), nullable=False, index=True)
    provider = Column(String(32), nullable=False, default="aws")
    scope_type = Column(String(32), nullable=False, default="cloud_account")
    scope_id = Column(UUID(as_uuid=True), nullable=True)

    idempotency_key = Column(String(256), nullable=False)
    status = Column(String(24), nullable=False, default="queued", index=True)
    priority = Column(Integer, nullable=False, default=100)
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=3)

    scheduled_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    failed_at = Column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at = Column(DateTime(timezone=True), nullable=True)

    payload_json = Column(JSONB().with_variant(JSON(), "sqlite"), nullable=True)
    result_json = Column(JSONB().with_variant(JSON(), "sqlite"), nullable=True)
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    worker_id = Column(String(128), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    def __repr__(self) -> str:
        return f"<SyncTask(id={self.id}, type={self.task_type}, status={self.status})>"


class SyncJobEvent(Base):
    __tablename__ = "sync_job_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    sync_job_id = Column(UUID(as_uuid=True), ForeignKey("sync_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    sync_task_id = Column(UUID(as_uuid=True), ForeignKey("sync_tasks.id", ondelete="SET NULL"), nullable=True, index=True)

    event_type = Column(String(64), nullable=False, index=True)
    level = Column(String(16), nullable=False, default="info")
    message = Column(Text, nullable=False)
    details_json = Column(JSONB().with_variant(JSON(), "sqlite"), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, index=True)

    def __repr__(self) -> str:
        return f"<SyncJobEvent(job={self.sync_job_id}, type={self.event_type})>"
