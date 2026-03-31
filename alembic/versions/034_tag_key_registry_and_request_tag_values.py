"""Add account tag-key registry and approval/outcome tag payload columns.

Revision ID: 034_tag_keys
Revises: 033_cost_policy_hardening
Create Date: 2026-03-31
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "034_tag_keys"
down_revision = "033_cost_policy_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_tag_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cloud_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key_name", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cloud_account_id"], ["cloud_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cloud_account_id", "key_name", name="uq_account_tag_keys_cloud_key"),
    )
    op.create_index(op.f("ix_account_tag_keys_cloud_account_id"), "account_tag_keys", ["cloud_account_id"], unique=False)
    op.add_column("approval_requests", sa.Column("requested_tag_values_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column(
        "recommendation_outcomes",
        sa.Column("tag_values_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recommendation_outcomes", "tag_values_json")
    op.drop_column("approval_requests", "requested_tag_values_json")
    op.drop_index(op.f("ix_account_tag_keys_cloud_account_id"), table_name="account_tag_keys")
    op.drop_table("account_tag_keys")
