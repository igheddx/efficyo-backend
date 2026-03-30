"""Add impact and follow-up fields to recommendation_outcomes.

Revision ID: 011
Revises: 010
Create Date: 2026-03-25 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recommendation_outcomes",
        sa.Column("impact_status", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "recommendation_outcomes",
        sa.Column("impact_summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "recommendation_outcomes",
        sa.Column("follow_up_recommendation", sa.Text(), nullable=True),
    )
    op.add_column(
        "recommendation_outcomes",
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recommendation_outcomes", "last_evaluated_at")
    op.drop_column("recommendation_outcomes", "follow_up_recommendation")
    op.drop_column("recommendation_outcomes", "impact_summary")
    op.drop_column("recommendation_outcomes", "impact_status")
