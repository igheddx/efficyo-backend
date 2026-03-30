"""Add estimated_savings to recommendations.

Revision ID: 006
Revises: 005
Create Date: 2026-03-24 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recommendations",
        sa.Column("estimated_savings", sa.Numeric(12, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recommendations", "estimated_savings")
