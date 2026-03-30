"""Multi-approver approval requests and assignments.

Revision ID: 024
Revises: 023
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approval_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cloud_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submitted_by", sa.String(length=320), nullable=True),
        sa.Column("submitted_by_role", sa.String(length=32), nullable=True),
        sa.Column("approval_mode", sa.String(length=32), nullable=False, server_default="all_required"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="submitted"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cloud_account_id"], ["cloud_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_approval_requests_org_id", "approval_requests", ["organization_id"], unique=False)
    op.create_index("ix_approval_requests_tenant_cloud_rec", "approval_requests", ["tenant_id", "cloud_account_id", "recommendation_id"], unique=False)
    op.create_index("ix_approval_requests_status", "approval_requests", ["status"], unique=False)

    op.create_table(
        "approval_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approval_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approver_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approver_name_snapshot", sa.String(length=255), nullable=False),
        sa.Column("approver_role_snapshot", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("acted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["approval_request_id"], ["approval_requests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["approver_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_approval_assignments_request_id", "approval_assignments", ["approval_request_id"], unique=False)
    op.create_index("ix_approval_assignments_user_id", "approval_assignments", ["approver_user_id"], unique=False)
    op.create_index("ix_approval_assignments_status", "approval_assignments", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_approval_assignments_status", table_name="approval_assignments")
    op.drop_index("ix_approval_assignments_user_id", table_name="approval_assignments")
    op.drop_index("ix_approval_assignments_request_id", table_name="approval_assignments")
    op.drop_table("approval_assignments")
    op.drop_index("ix_approval_requests_status", table_name="approval_requests")
    op.drop_index("ix_approval_requests_tenant_cloud_rec", table_name="approval_requests")
    op.drop_index("ix_approval_requests_org_id", table_name="approval_requests")
    op.drop_table("approval_requests")
