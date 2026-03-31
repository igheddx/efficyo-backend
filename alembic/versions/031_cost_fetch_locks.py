"""Add cost fetch locks table for duplicate-call prevention.

Revision ID: 031_cost_fetch_locks
Revises: 030_cost_snapshot_architecture
Create Date: 2026-03-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "031_cost_fetch_locks"
down_revision = "030_cost_snapshot_architecture"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cost_fetch_locks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cloud_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="aws"),
        sa.Column("request_signature", sa.String(length=128), nullable=False),
        sa.Column("lock_reason", sa.String(length=64), nullable=False, server_default="cost_sync"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cloud_account_id"], ["cloud_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_signature"),
    )
    op.create_index("ix_cost_fetch_locks_org_id", "cost_fetch_locks", ["org_id"], unique=False)
    op.create_index("ix_cost_fetch_locks_tenant_id", "cost_fetch_locks", ["tenant_id"], unique=False)
    op.create_index("ix_cost_fetch_locks_cloud_account_id", "cost_fetch_locks", ["cloud_account_id"], unique=False)
    op.create_index("ix_cost_fetch_locks_provider", "cost_fetch_locks", ["provider"], unique=False)
    op.create_index("ix_cost_fetch_locks_locked_until", "cost_fetch_locks", ["locked_until"], unique=False)
    op.create_index("ix_cost_fetch_locks_request_signature", "cost_fetch_locks", ["request_signature"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_cost_fetch_locks_request_signature", table_name="cost_fetch_locks")
    op.drop_index("ix_cost_fetch_locks_locked_until", table_name="cost_fetch_locks")
    op.drop_index("ix_cost_fetch_locks_provider", table_name="cost_fetch_locks")
    op.drop_index("ix_cost_fetch_locks_cloud_account_id", table_name="cost_fetch_locks")
    op.drop_index("ix_cost_fetch_locks_tenant_id", table_name="cost_fetch_locks")
    op.drop_index("ix_cost_fetch_locks_org_id", table_name="cost_fetch_locks")
    op.drop_table("cost_fetch_locks")

