"""Add confidence_score to recommendations.

Revision ID: 007
Revises: 006
Create Date: 2026-03-24 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recommendations",
        sa.Column("confidence_score", sa.String(length=20), nullable=False, server_default="medium"),
    )
    op.alter_column("recommendations", "confidence_score", server_default=None)


def downgrade() -> None:
    op.drop_column("recommendations", "confidence_score")
