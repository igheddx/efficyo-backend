"""Add execution_role_arn to cloud_accounts."""

from alembic import op
import sqlalchemy as sa

revision = "044_cloud_acct_exec_role"
down_revision = "043_approval_executed_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cloud_accounts",
        sa.Column("execution_role_arn", sa.String(512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cloud_accounts", "execution_role_arn")
