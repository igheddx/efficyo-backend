"""Execution owner assignment table for approval accountability.

Revision ID: 032_execution_owner_assignments
Revises: 031_cost_fetch_locks
Create Date: 2026-03-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "032_execution_owner_assignments"
down_revision = "031_cost_fetch_locks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_owner_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cloud_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approval_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_name_snapshot", sa.String(length=255), nullable=False),
        sa.Column("owner_role_snapshot", sa.String(length=32), nullable=False),
        sa.Column("assigned_by", sa.String(length=320), nullable=True),
        sa.Column("assigned_by_role", sa.String(length=32), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cloud_account_id"], ["cloud_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["approval_request_id"], ["approval_requests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("approval_request_id", name="uq_execution_owner_by_request"),
    )
    op.create_index("ix_execution_owner_assignments_organization_id", "execution_owner_assignments", ["organization_id"], unique=False)
    op.create_index("ix_execution_owner_assignments_tenant_id", "execution_owner_assignments", ["tenant_id"], unique=False)
    op.create_index("ix_execution_owner_assignments_cloud_account_id", "execution_owner_assignments", ["cloud_account_id"], unique=False)
    op.create_index("ix_execution_owner_assignments_recommendation_id", "execution_owner_assignments", ["recommendation_id"], unique=False)
    op.create_index("ix_execution_owner_assignments_approval_request_id", "execution_owner_assignments", ["approval_request_id"], unique=False)
    op.create_index("ix_execution_owner_assignments_owner_user_id", "execution_owner_assignments", ["owner_user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_execution_owner_assignments_owner_user_id", table_name="execution_owner_assignments")
    op.drop_index("ix_execution_owner_assignments_approval_request_id", table_name="execution_owner_assignments")
    op.drop_index("ix_execution_owner_assignments_recommendation_id", table_name="execution_owner_assignments")
    op.drop_index("ix_execution_owner_assignments_cloud_account_id", table_name="execution_owner_assignments")
    op.drop_index("ix_execution_owner_assignments_tenant_id", table_name="execution_owner_assignments")
    op.drop_index("ix_execution_owner_assignments_organization_id", table_name="execution_owner_assignments")
    op.drop_table("execution_owner_assignments")

