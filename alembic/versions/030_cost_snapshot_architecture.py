"""Cost snapshot architecture tables and indexes.

Revision ID: 030_cost_snapshot_architecture
Revises: 029_sync_pipeline
Create Date: 2026-03-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "030_cost_snapshot_architecture"
down_revision = "029_sync_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cost_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cloud_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="aws"),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("granularity", sa.String(length=16), nullable=False, server_default="DAILY"),
        sa.Column("total_cost", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="USD"),
        sa.Column("service_breakdown_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("daily_costs_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("cost_trends_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("ec2_other_breakdown_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("waf_monthly_cost", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("source_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("freshness_status", sa.String(length=24), nullable=False, server_default="fresh"),
        sa.Column("stale_after_minutes", sa.Integer(), nullable=False, server_default="1440"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cloud_account_id"], ["cloud_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_job_id"], ["ingestion_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cost_snapshots_org_id", "cost_snapshots", ["org_id"], unique=False)
    op.create_index("ix_cost_snapshots_tenant_id", "cost_snapshots", ["tenant_id"], unique=False)
    op.create_index("ix_cost_snapshots_cloud_account_id", "cost_snapshots", ["cloud_account_id"], unique=False)
    op.create_index("ix_cost_snapshots_provider", "cost_snapshots", ["provider"], unique=False)
    op.create_index("ix_cost_snapshots_snapshot_date", "cost_snapshots", ["snapshot_date"], unique=False)
    op.create_index("ix_cost_snapshots_created_at", "cost_snapshots", ["created_at"], unique=False)
    op.create_index(
        "uq_cost_snapshots_scope_day",
        "cost_snapshots",
        ["tenant_id", "cloud_account_id", "provider", "snapshot_date"],
        unique=True,
    )

    op.create_table(
        "cost_api_usage_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cloud_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="aws"),
        sa.Column("sync_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("feature_name", sa.String(length=64), nullable=False),
        sa.Column("request_type", sa.String(length=64), nullable=False),
        sa.Column("request_signature", sa.String(length=128), nullable=False),
        sa.Column("was_cache_hit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("api_name", sa.String(length=64), nullable=False),
        sa.Column("estimated_call_cost", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cloud_account_id"], ["cloud_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sync_job_id"], ["ingestion_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cost_api_usage_log_org_id", "cost_api_usage_log", ["org_id"], unique=False)
    op.create_index("ix_cost_api_usage_log_tenant_id", "cost_api_usage_log", ["tenant_id"], unique=False)
    op.create_index("ix_cost_api_usage_log_cloud_account_id", "cost_api_usage_log", ["cloud_account_id"], unique=False)
    op.create_index("ix_cost_api_usage_log_provider", "cost_api_usage_log", ["provider"], unique=False)
    op.create_index("ix_cost_api_usage_log_sync_job_id", "cost_api_usage_log", ["sync_job_id"], unique=False)
    op.create_index("ix_cost_api_usage_log_request_signature", "cost_api_usage_log", ["request_signature"], unique=False)
    op.create_index("ix_cost_api_usage_log_created_at", "cost_api_usage_log", ["created_at"], unique=False)

    op.create_table(
        "cost_sync_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cloud_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="aws"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sync_frequency", sa.String(length=32), nullable=False, server_default="daily"),
        sa.Column("max_calls_per_day", sa.Integer(), nullable=False, server_default="25"),
        sa.Column("max_calls_per_job", sa.Integer(), nullable=False, server_default="6"),
        sa.Column("stale_after_minutes", sa.Integer(), nullable=False, server_default="1440"),
        sa.Column("hard_stop_on_quota", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cloud_account_id"], ["cloud_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cost_sync_policies_org_id", "cost_sync_policies", ["org_id"], unique=False)
    op.create_index("ix_cost_sync_policies_tenant_id", "cost_sync_policies", ["tenant_id"], unique=False)
    op.create_index("ix_cost_sync_policies_cloud_account_id", "cost_sync_policies", ["cloud_account_id"], unique=False)
    op.create_index("ix_cost_sync_policies_provider", "cost_sync_policies", ["provider"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_cost_sync_policies_provider", table_name="cost_sync_policies")
    op.drop_index("ix_cost_sync_policies_cloud_account_id", table_name="cost_sync_policies")
    op.drop_index("ix_cost_sync_policies_tenant_id", table_name="cost_sync_policies")
    op.drop_index("ix_cost_sync_policies_org_id", table_name="cost_sync_policies")
    op.drop_table("cost_sync_policies")

    op.drop_index("ix_cost_api_usage_log_created_at", table_name="cost_api_usage_log")
    op.drop_index("ix_cost_api_usage_log_request_signature", table_name="cost_api_usage_log")
    op.drop_index("ix_cost_api_usage_log_sync_job_id", table_name="cost_api_usage_log")
    op.drop_index("ix_cost_api_usage_log_provider", table_name="cost_api_usage_log")
    op.drop_index("ix_cost_api_usage_log_cloud_account_id", table_name="cost_api_usage_log")
    op.drop_index("ix_cost_api_usage_log_tenant_id", table_name="cost_api_usage_log")
    op.drop_index("ix_cost_api_usage_log_org_id", table_name="cost_api_usage_log")
    op.drop_table("cost_api_usage_log")

    op.drop_index("uq_cost_snapshots_scope_day", table_name="cost_snapshots")
    op.drop_index("ix_cost_snapshots_created_at", table_name="cost_snapshots")
    op.drop_index("ix_cost_snapshots_snapshot_date", table_name="cost_snapshots")
    op.drop_index("ix_cost_snapshots_provider", table_name="cost_snapshots")
    op.drop_index("ix_cost_snapshots_cloud_account_id", table_name="cost_snapshots")
    op.drop_index("ix_cost_snapshots_tenant_id", table_name="cost_snapshots")
    op.drop_index("ix_cost_snapshots_org_id", table_name="cost_snapshots")
    op.drop_table("cost_snapshots")

