"""Recommendation outcomes: approval comment, rejection audit fields.

Revision ID: 022
Revises: 021
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recommendation_outcomes",
        sa.Column("approval_comment", sa.Text(), nullable=True),
    )
    op.add_column(
        "recommendation_outcomes",
        sa.Column("rejection_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "recommendation_outcomes",
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "recommendation_outcomes",
        sa.Column("rejected_by", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recommendation_outcomes", "rejected_by")
    op.drop_column("recommendation_outcomes", "rejected_at")
    op.drop_column("recommendation_outcomes", "rejection_reason")
    op.drop_column("recommendation_outcomes", "approval_comment")
