"""Allow NULL password_hash for OIDC-only users (JIT provisioning).

Revision ID: 020
Revises: 019
Create Date: 2026-03-28 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("users", "password_hash", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    from passlib.hash import bcrypt

    placeholder = bcrypt.hash("__oidc_placeholder_reset_password__")
    conn = op.get_bind()
    conn.execute(
        sa.text("UPDATE users SET password_hash = :h WHERE password_hash IS NULL"),
        {"h": placeholder},
    )
    op.alter_column("users", "password_hash", existing_type=sa.Text(), nullable=False)
