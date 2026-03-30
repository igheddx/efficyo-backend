"""User default org/tenant/cloud context preferences.

Revision ID: 026
Revises: 025
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "default_organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "default_tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "default_cloud_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cloud_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(op.f("ix_users_default_organization_id"), "users", ["default_organization_id"], unique=False)
    op.create_index(op.f("ix_users_default_tenant_id"), "users", ["default_tenant_id"], unique=False)
    op.create_index(op.f("ix_users_default_cloud_account_id"), "users", ["default_cloud_account_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_users_default_cloud_account_id"), table_name="users")
    op.drop_index(op.f("ix_users_default_tenant_id"), table_name="users")
    op.drop_index(op.f("ix_users_default_organization_id"), table_name="users")
    op.drop_column("users", "default_cloud_account_id")
    op.drop_column("users", "default_tenant_id")
    op.drop_column("users", "default_organization_id")
