"""Proof-of-savings fields on recommendation_outcomes (rolling 30d account cost basis)."""

from alembic import op
import sqlalchemy as sa


revision = "027_proof_of_savings"
down_revision = "026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recommendation_outcomes",
        sa.Column("before_cost", sa.Numeric(14, 2), nullable=True),
    )
    op.add_column(
        "recommendation_outcomes",
        sa.Column("after_cost", sa.Numeric(14, 2), nullable=True),
    )
    op.add_column(
        "recommendation_outcomes",
        sa.Column("estimated_savings", sa.Numeric(14, 2), nullable=True),
    )
    op.add_column(
        "recommendation_outcomes",
        sa.Column("savings_verified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recommendation_outcomes", "savings_verified_at")
    op.drop_column("recommendation_outcomes", "estimated_savings")
    op.drop_column("recommendation_outcomes", "after_cost")
    op.drop_column("recommendation_outcomes", "before_cost")
