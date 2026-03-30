"""Execution policy layer + audit + preflight / auto-apply markers on outcomes."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "028_execution_policies"
down_revision = "027_proof_of_savings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cloud_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("recommendation_type", sa.String(length=100), nullable=False),
        sa.Column("risk_class", sa.String(length=20), nullable=False, server_default="any"),
        sa.Column("execution_mode", sa.String(length=40), nullable=False),
        sa.Column("requires_all_approvals", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("preflight_required", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("rollback_required", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by_email", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["cloud_account_id"], ["cloud_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_execution_policies_org", "execution_policies", ["organization_id"], unique=False)
    op.create_index("ix_execution_policies_tenant", "execution_policies", ["tenant_id"], unique=False)
    op.create_index("ix_execution_policies_cloud", "execution_policies", ["cloud_account_id"], unique=False)
    op.create_index("ix_execution_policies_type", "execution_policies", ["recommendation_type"], unique=False)

    op.create_table(
        "execution_audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cloud_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("execution_policy_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_email", sa.String(length=255), nullable=True),
        sa.Column("execution_trigger", sa.String(length=24), nullable=True),
        sa.Column("allowed", sa.Boolean(), nullable=True),
        sa.Column("blocking_reason", sa.Text(), nullable=True),
        sa.Column("detail_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["execution_policy_id"],
            ["execution_policies.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_exec_audit_created", "execution_audit_events", ["created_at"], unique=False)
    op.create_index("ix_exec_audit_rec", "execution_audit_events", ["recommendation_id"], unique=False)

    op.add_column(
        "recommendation_outcomes",
        sa.Column("preflight_passed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "recommendation_outcomes",
        sa.Column("applied_via_auto", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.alter_column("recommendation_outcomes", "applied_via_auto", server_default=None)


def downgrade() -> None:
    op.drop_column("recommendation_outcomes", "applied_via_auto")
    op.drop_column("recommendation_outcomes", "preflight_passed_at")
    op.drop_index("ix_exec_audit_rec", table_name="execution_audit_events")
    op.drop_index("ix_exec_audit_created", table_name="execution_audit_events")
    op.drop_table("execution_audit_events")
    op.drop_index("ix_execution_policies_type", table_name="execution_policies")
    op.drop_index("ix_execution_policies_cloud", table_name="execution_policies")
    op.drop_index("ix_execution_policies_tenant", table_name="execution_policies")
    op.drop_index("ix_execution_policies_org", table_name="execution_policies")
    op.drop_table("execution_policies")
