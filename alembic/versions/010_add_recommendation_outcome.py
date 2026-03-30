"""Add recommendation_outcomes savings proof table.

Revision ID: 010
Revises: 009
Create Date: 2026-03-26 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recommendation_outcomes",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cloud_account_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recommendation_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=False),
        sa.Column("recommendation_type", sa.String(length=100), nullable=False),
        sa.Column("recommendation_category", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("acted_on_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("baseline_monthly_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("current_monthly_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("estimated_savings_at_action", sa.Numeric(12, 2), nullable=True),
        sa.Column("realized_savings", sa.Numeric(12, 2), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cloud_account_id"], ["cloud_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recommendation_id"], ["recommendations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("recommendation_id", name="uq_recommendation_outcomes_recommendation_id"),
    )


def downgrade() -> None:
    op.drop_table("recommendation_outcomes")

