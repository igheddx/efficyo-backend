"""Add receive_approval_emails preference to users table.

Revision ID: 042_user_approval_email_pref
Revises: 041_reconcile_key_unique
Create Date: 2026-04-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "042_user_approval_email_pref"
down_revision = "041_reconcile_key_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "receive_approval_emails",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "receive_approval_emails")
