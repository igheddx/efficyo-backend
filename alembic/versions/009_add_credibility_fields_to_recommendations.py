"""Add savings_basis and confidence_reason to recommendations.

Revision ID: 009
Revises: 008
Create Date: 2026-03-25 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recommendations", sa.Column("savings_basis", sa.Text(), nullable=True))
    op.add_column("recommendations", sa.Column("confidence_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("recommendations", "confidence_reason")
    op.drop_column("recommendations", "savings_basis")
