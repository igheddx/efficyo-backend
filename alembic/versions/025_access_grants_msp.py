"""Tenant/cloud access grants; migrate flat membership roles to member + grants.

Revision ID: 025
Revises: 024
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "access_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cloud_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("access_role", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cloud_account_id"], ["cloud_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_access_grants_tenant_wide",
        "access_grants",
        ["user_id", "organization_id", "tenant_id"],
        unique=True,
        postgresql_where=sa.text("cloud_account_id IS NULL"),
    )
    op.create_index(
        "uq_access_grants_account_scoped",
        "access_grants",
        ["user_id", "organization_id", "tenant_id", "cloud_account_id"],
        unique=True,
        postgresql_where=sa.text("cloud_account_id IS NOT NULL"),
    )
    op.create_index(op.f("ix_access_grants_organization_id"), "access_grants", ["organization_id"], unique=False)
    op.create_index(op.f("ix_access_grants_tenant_id"), "access_grants", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_access_grants_cloud_account_id"), "access_grants", ["cloud_account_id"], unique=False)
    op.create_index(op.f("ix_access_grants_user_id"), "access_grants", ["user_id"], unique=False)

    # Backfill: former admin/approver/viewer membership → tenant-wide grants per customer tenant in that org.
    op.execute(
        sa.text(
            """
            INSERT INTO access_grants (
                id, user_id, organization_id, tenant_id, cloud_account_id, access_role, created_at, updated_at
            )
            SELECT gen_random_uuid(), m.user_id, m.organization_id, t.id, NULL,
                CASE m.role
                    WHEN 'admin' THEN 'admin'
                    WHEN 'approver' THEN 'approver'
                    WHEN 'viewer' THEN 'viewer'
                END,
                NOW(), NOW()
            FROM org_memberships m
            INNER JOIN tenants t ON t.organization_id = m.organization_id
            WHERE m.user_id IS NOT NULL
              AND m.role IN ('admin', 'approver', 'viewer')
            """
        )
    )

    op.execute(
        sa.text(
            "UPDATE org_memberships SET role = 'member' WHERE role IN ('admin', 'approver', 'viewer')"
        )
    )

    op.add_column(
        "recommendation_outcomes",
        sa.Column("approved_membership_role", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "recommendation_outcomes",
        sa.Column("approved_access_role", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "recommendation_outcomes",
        sa.Column("applied_membership_role", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "recommendation_outcomes",
        sa.Column("applied_access_role", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recommendation_outcomes", "applied_access_role")
    op.drop_column("recommendation_outcomes", "applied_membership_role")
    op.drop_column("recommendation_outcomes", "approved_access_role")
    op.drop_column("recommendation_outcomes", "approved_membership_role")
    op.drop_index("uq_access_grants_account_scoped", table_name="access_grants")
    op.drop_index("uq_access_grants_tenant_wide", table_name="access_grants")
    op.drop_index(op.f("ix_access_grants_user_id"), table_name="access_grants")
    op.drop_index(op.f("ix_access_grants_cloud_account_id"), table_name="access_grants")
    op.drop_index(op.f("ix_access_grants_tenant_id"), table_name="access_grants")
    op.drop_index(op.f("ix_access_grants_organization_id"), table_name="access_grants")
    op.drop_table("access_grants")
