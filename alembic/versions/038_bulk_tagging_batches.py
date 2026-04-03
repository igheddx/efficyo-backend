"""Add bulk tagging batch workflow tables.

Revision ID: 038_bulk_tagging_batches
Revises: 037_cloud_account_external_id
Create Date: 2026-04-02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "038_bulk_tagging_batches"
down_revision = "037_cloud_account_external_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tagging_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cloud_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approval_request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("recommendation_type", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requested_by", sa.String(length=320), nullable=True),
        sa.Column("requested_by_role", sa.String(length=32), nullable=True),
        sa.Column("execution_notes", sa.Text(), nullable=True),
        sa.Column("required_tag_keys_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("shared_tags_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("summary_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["approval_request_id"], ["approval_requests.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["cloud_account_id"], ["cloud_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("approval_request_id"),
    )
    op.create_index(op.f("ix_tagging_batches_cloud_account_id"), "tagging_batches", ["cloud_account_id"], unique=False)
    op.create_index(op.f("ix_tagging_batches_organization_id"), "tagging_batches", ["organization_id"], unique=False)
    op.create_index(op.f("ix_tagging_batches_tenant_id"), "tagging_batches", ["tenant_id"], unique=False)

    op.create_table(
        "tagging_batch_resources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("current_tags_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("missing_required_tags_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("proposed_tags_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("context_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("execution_status", sa.String(length=32), nullable=False),
        sa.Column("execution_error", sa.Text(), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["tagging_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recommendation_id"], ["recommendations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tagging_batch_resources_batch_id"), "tagging_batch_resources", ["batch_id"], unique=False)
    op.create_index(op.f("ix_tagging_batch_resources_recommendation_id"), "tagging_batch_resources", ["recommendation_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_tagging_batch_resources_recommendation_id"), table_name="tagging_batch_resources")
    op.drop_index(op.f("ix_tagging_batch_resources_batch_id"), table_name="tagging_batch_resources")
    op.drop_table("tagging_batch_resources")

    op.drop_index(op.f("ix_tagging_batches_tenant_id"), table_name="tagging_batches")
    op.drop_index(op.f("ix_tagging_batches_organization_id"), table_name="tagging_batches")
    op.drop_index(op.f("ix_tagging_batches_cloud_account_id"), table_name="tagging_batches")
    op.drop_table("tagging_batches")
