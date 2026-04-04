"""Add executed_at to approval_requests."""

from alembic import op
import sqlalchemy as sa

revision = "043_approval_executed_at"
down_revision = "042_user_approval_email_pref"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "approval_requests",
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("approval_requests", "executed_at")
