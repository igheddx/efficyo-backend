"""Add org_integrations table for Slack (and future) org-level notification channels."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "046_org_integrations"
down_revision = "045_cloud_acct_cf_onboarding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "org_integrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("is_enabled", sa.Boolean, nullable=False, default=True),
        sa.Column("webhook_url", sa.Text, nullable=True),
        sa.Column("channel_name", sa.String(128), nullable=True),
        sa.Column("last_test_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_digest_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_delivery_status", sa.String(16), nullable=True),
        sa.Column("last_delivery_error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "provider", name="uq_org_integrations_org_provider"),
    )


def downgrade() -> None:
    op.drop_table("org_integrations")
