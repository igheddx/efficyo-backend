"""Add invite onboarding fields to users.

Revision ID: 036_user_invite_fields
Revises: 035_platform_alert_thresholds
Create Date: 2026-04-02
"""

from alembic import op
import sqlalchemy as sa

revision = "036_user_invite_fields"
down_revision = "035_platform_alert_thresholds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "users",
        sa.Column("temporary_password_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "temporary_password_expires_at")
    op.drop_column("users", "must_change_password")
