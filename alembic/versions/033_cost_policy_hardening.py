"""Harden cost sync policy with org quota and force-refresh flag.

Revision ID: 033_cost_policy_hardening
Revises: 032_execution_owner_assignments
Create Date: 2026-03-30
"""

from alembic import op
import sqlalchemy as sa

revision = "033_cost_policy_hardening"
down_revision = "032_execution_owner_assignments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cost_sync_policies",
        sa.Column("max_calls_per_org_day", sa.Integer(), nullable=False, server_default="250"),
    )
    op.add_column(
        "cost_sync_policies",
        sa.Column("allow_admin_force_refresh", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("cost_sync_policies", "max_calls_per_org_day", server_default=None)
    op.alter_column("cost_sync_policies", "allow_admin_force_refresh", server_default=None)


def downgrade() -> None:
    op.drop_column("cost_sync_policies", "allow_admin_force_refresh")
    op.drop_column("cost_sync_policies", "max_calls_per_org_day")

