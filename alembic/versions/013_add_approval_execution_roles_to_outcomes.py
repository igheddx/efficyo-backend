"""Add approved_role and applied_role to recommendation_outcomes.

Revision ID: 013
Revises: 012
Create Date: 2026-03-27 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recommendation_outcomes", sa.Column("approved_role", sa.String(length=20), nullable=True))
    op.add_column("recommendation_outcomes", sa.Column("applied_role", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("recommendation_outcomes", "applied_role")
    op.drop_column("recommendation_outcomes", "approved_role")

