from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.db import Base, utc_now


class CostSnapshot(Base):
    __tablename__ = "cost_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    cloud_account_id = Column(
        UUID(as_uuid=True), ForeignKey("cloud_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider = Column(String(32), nullable=False, default="aws", index=True)
    snapshot_date = Column(Date, nullable=False, index=True)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    granularity = Column(String(16), nullable=False, default="DAILY")
    total_cost = Column(Numeric(18, 4), nullable=False, default=0)
    currency = Column(String(8), nullable=False, default="USD")
    service_breakdown_json = Column(JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list)
    daily_costs_json = Column(JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list)
    cost_trends_json = Column(JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list)
    ec2_other_breakdown_json = Column(JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list)
    waf_monthly_cost = Column(Numeric(18, 4), nullable=False, default=0)
    source_job_id = Column(UUID(as_uuid=True), ForeignKey("ingestion_jobs.id", ondelete="SET NULL"), nullable=True)
    freshness_status = Column(String(24), nullable=False, default="fresh")
    stale_after_minutes = Column(Integer, nullable=False, default=1440)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, index=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        Index(
            "uq_cost_snapshots_scope_day",
            "tenant_id",
            "cloud_account_id",
            "provider",
            "snapshot_date",
            unique=True,
        ),
    )


class CostApiUsageLog(Base):
    __tablename__ = "cost_api_usage_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    cloud_account_id = Column(
        UUID(as_uuid=True), ForeignKey("cloud_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider = Column(String(32), nullable=False, default="aws", index=True)
    sync_job_id = Column(UUID(as_uuid=True), ForeignKey("ingestion_jobs.id", ondelete="SET NULL"), nullable=True, index=True)
    feature_name = Column(String(64), nullable=False)
    request_type = Column(String(64), nullable=False)
    request_signature = Column(String(128), nullable=False, index=True)
    was_cache_hit = Column(Boolean, nullable=False, default=False)
    api_name = Column(String(64), nullable=False)
    estimated_call_cost = Column(Numeric(18, 6), nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, index=True)


class CostSyncPolicy(Base):
    __tablename__ = "cost_sync_policies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    cloud_account_id = Column(
        UUID(as_uuid=True), ForeignKey("cloud_accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    provider = Column(String(32), nullable=False, default="aws", index=True)
    enabled = Column(Boolean, nullable=False, default=True)
    sync_frequency = Column(String(32), nullable=False, default="daily")
    max_calls_per_day = Column(Integer, nullable=False, default=25)
    max_calls_per_org_day = Column(Integer, nullable=False, default=250)
    max_calls_per_job = Column(Integer, nullable=False, default=6)
    stale_after_minutes = Column(Integer, nullable=False, default=1440)
    hard_stop_on_quota = Column(Boolean, nullable=False, default=True)
    allow_admin_force_refresh = Column(Boolean, nullable=False, default=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class CostFetchLock(Base):
    __tablename__ = "cost_fetch_locks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    cloud_account_id = Column(
        UUID(as_uuid=True), ForeignKey("cloud_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider = Column(String(32), nullable=False, default="aws", index=True)
    request_signature = Column(String(128), nullable=False, unique=True, index=True)
    lock_reason = Column(String(64), nullable=False, default="cost_sync")
    locked_until = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

