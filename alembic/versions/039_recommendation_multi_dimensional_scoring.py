"""Add multi-dimensional scoring fields to recommendations.

Revision ID: 039_multi_dim_scoring
Revises: 038_bulk_tagging_batches
Create Date: 2026-04-03
"""

from alembic import op
import sqlalchemy as sa


revision = "039_multi_dim_scoring"
down_revision = "038_bulk_tagging_batches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recommendations", sa.Column("impact_score", sa.String(length=20), nullable=True))
    op.add_column("recommendations", sa.Column("effort_score", sa.String(length=20), nullable=True))
    op.add_column("recommendations", sa.Column("actionability_type", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("recommendations", "actionability_type")
    op.drop_column("recommendations", "effort_score")
    op.drop_column("recommendations", "impact_score")