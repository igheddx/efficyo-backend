"""Add CloudFormation onboarding fields to cloud_accounts."""

from alembic import op
import sqlalchemy as sa

revision = "045_cloud_acct_cf_onboarding"
down_revision = "044_cloud_acct_exec_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cloud_accounts",
        sa.Column("onboarding_mode", sa.String(32), nullable=True),
    )
    op.add_column(
        "cloud_accounts",
        sa.Column("read_only_status", sa.String(32), nullable=True),
    )
    op.add_column(
        "cloud_accounts",
        sa.Column("execution_status", sa.String(32), nullable=True),
    )
    op.add_column(
        "cloud_accounts",
        sa.Column("cf_stack_launched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "cloud_accounts",
        sa.Column(
            "created_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "cloud_accounts",
        sa.Column("onboarding_token", sa.String(128), nullable=True),
    )
    op.create_unique_constraint(
        "uq_cloud_accounts_onboarding_token",
        "cloud_accounts",
        ["onboarding_token"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_cloud_accounts_onboarding_token", "cloud_accounts")
    op.drop_column("cloud_accounts", "onboarding_token")
    op.drop_column("cloud_accounts", "created_by_user_id")
    op.drop_column("cloud_accounts", "cf_stack_launched_at")
    op.drop_column("cloud_accounts", "execution_status")
    op.drop_column("cloud_accounts", "read_only_status")
    op.drop_column("cloud_accounts", "onboarding_mode")
