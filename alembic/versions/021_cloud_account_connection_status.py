"""Cloud account onboarding: connection_status and validation audit fields.

Revision ID: 021
Revises: 020
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cloud_accounts",
        sa.Column(
            "connection_status",
            sa.String(length=32),
            nullable=False,
            server_default="untested",
        ),
    )
    op.add_column(
        "cloud_accounts",
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "cloud_accounts",
        sa.Column("last_validation_error", sa.Text(), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE cloud_accounts SET connection_status = CASE
                WHEN status = 'connected' THEN 'valid'
                WHEN status = 'failed' THEN 'invalid'
                ELSE 'untested'
            END
            """
        )
    )
    op.alter_column("cloud_accounts", "connection_status", server_default=None)


def downgrade() -> None:
    op.drop_column("cloud_accounts", "last_validation_error")
    op.drop_column("cloud_accounts", "last_validated_at")
    op.drop_column("cloud_accounts", "connection_status")
