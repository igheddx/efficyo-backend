"""Sync pipeline: sync_jobs, sync_tasks, sync_job_events (orchestrator + workers)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "029_sync_pipeline"
down_revision = "028_execution_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cloud_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="aws"),
        sa.Column("initiated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trigger_type", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_tasks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_tasks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_tasks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_tasks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("force_new", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cloud_account_id"], ["cloud_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["initiated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sync_jobs_org", "sync_jobs", ["organization_id"], unique=False)
    op.create_index("ix_sync_jobs_tenant", "sync_jobs", ["tenant_id"], unique=False)
    op.create_index("ix_sync_jobs_cloud", "sync_jobs", ["cloud_account_id"], unique=False)
    op.create_index("ix_sync_jobs_provider", "sync_jobs", ["provider"], unique=False)
    op.create_index("ix_sync_jobs_status", "sync_jobs", ["status"], unique=False)

    op.create_table(
        "sync_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sync_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("task_category", sa.String(length=24), nullable=False),
        sa.Column("task_type", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="aws"),
        sa.Column("scope_type", sa.String(length=32), nullable=False, server_default="cloud_account"),
        sa.Column("scope_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["parent_task_id"], ["sync_tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["sync_job_id"], ["sync_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sync_job_id", "idempotency_key", name="uq_sync_tasks_job_idempotency"),
    )
    op.create_index("ix_sync_tasks_job", "sync_tasks", ["sync_job_id"], unique=False)
    op.create_index("ix_sync_tasks_status", "sync_tasks", ["status"], unique=False)
    op.create_index("ix_sync_tasks_category", "sync_tasks", ["task_category"], unique=False)
    op.create_index("ix_sync_tasks_type", "sync_tasks", ["task_type"], unique=False)

    op.create_table(
        "sync_job_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sync_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sync_task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False, server_default="info"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["sync_job_id"], ["sync_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sync_task_id"], ["sync_tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sync_job_events_job", "sync_job_events", ["sync_job_id"], unique=False)
    op.create_index("ix_sync_job_events_task", "sync_job_events", ["sync_task_id"], unique=False)
    op.create_index("ix_sync_job_events_type", "sync_job_events", ["event_type"], unique=False)
    op.create_index("ix_sync_job_events_created", "sync_job_events", ["created_at"], unique=False)

    op.execute(
        """
        CREATE UNIQUE INDEX uq_sync_jobs_active_scope
        ON sync_jobs (tenant_id, cloud_account_id, provider)
        WHERE status IN (
            'queued', 'planning', 'collecting', 'analyzing', 'scoring', 'summarizing'
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_sync_jobs_active_scope;")
    op.drop_index("ix_sync_job_events_created", table_name="sync_job_events")
    op.drop_index("ix_sync_job_events_type", table_name="sync_job_events")
    op.drop_index("ix_sync_job_events_task", table_name="sync_job_events")
    op.drop_index("ix_sync_job_events_job", table_name="sync_job_events")
    op.drop_table("sync_job_events")
    op.drop_index("ix_sync_tasks_type", table_name="sync_tasks")
    op.drop_index("ix_sync_tasks_category", table_name="sync_tasks")
    op.drop_index("ix_sync_tasks_status", table_name="sync_tasks")
    op.drop_index("ix_sync_tasks_job", table_name="sync_tasks")
    op.drop_table("sync_tasks")
    op.drop_index("ix_sync_jobs_status", table_name="sync_jobs")
    op.drop_index("ix_sync_jobs_provider", table_name="sync_jobs")
    op.drop_index("ix_sync_jobs_cloud", table_name="sync_jobs")
    op.drop_index("ix_sync_jobs_tenant", table_name="sync_jobs")
    op.drop_index("ix_sync_jobs_org", table_name="sync_jobs")
    op.drop_table("sync_jobs")
