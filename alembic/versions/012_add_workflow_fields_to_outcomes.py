"""Add workflow tracking fields to recommendation_outcomes.

Revision ID: 012
Revises: 011
Create Date: 2026-03-26 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recommendation_outcomes",
        sa.Column("workflow_status", sa.String(length=20), nullable=False, server_default="suggested"),
    )
    op.add_column("recommendation_outcomes", sa.Column("approved_by", sa.String(length=255), nullable=True))
    op.add_column("recommendation_outcomes", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("recommendation_outcomes", sa.Column("applied_by", sa.String(length=255), nullable=True))
    op.add_column("recommendation_outcomes", sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("recommendation_outcomes", sa.Column("execution_notes", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("recommendation_outcomes", "execution_notes")
    op.drop_column("recommendation_outcomes", "applied_at")
    op.drop_column("recommendation_outcomes", "applied_by")
    op.drop_column("recommendation_outcomes", "approved_at")
    op.drop_column("recommendation_outcomes", "approved_by")
    op.drop_column("recommendation_outcomes", "workflow_status")

