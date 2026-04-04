"""add recommendation lifecycle state fields

Revision ID: 040_recommendation_lifecycle_state
Revises: 039_recommendation_multi_dimensional_scoring
Create Date: 2026-04-03 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "040_lifecycle_state"
down_revision = "039_multi_dim_scoring"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recommendations", sa.Column("finding_type", sa.String(length=120), nullable=True))
    op.add_column("recommendations", sa.Column("state", sa.String(length=24), nullable=False, server_default="active"))
    op.add_column("recommendations", sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("recommendations", sa.Column("snoozed_by", sa.String(length=255), nullable=True))
    op.add_column("recommendations", sa.Column("dismissed_reason", sa.String(length=64), nullable=True))
    op.add_column("recommendations", sa.Column("dismissed_reason_note", sa.Text(), nullable=True))
    op.add_column("recommendations", sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("recommendations", sa.Column("dismissed_by", sa.String(length=255), nullable=True))
    op.add_column("recommendations", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("recommendations", sa.Column("resolution_source", sa.String(length=24), nullable=True))

    op.execute(
        """
        UPDATE recommendations AS r
        SET finding_type = f.finding_type
        FROM findings AS f
        WHERE r.finding_id = f.id
          AND (r.finding_type IS NULL OR r.finding_type = '')
        """
    )
    op.execute("UPDATE recommendations SET finding_type = '' WHERE finding_type IS NULL")
    op.alter_column("recommendations", "finding_type", nullable=False)

    op.create_index(
        "ix_recommendations_reconcile_key",
        "recommendations",
        ["tenant_id", "cloud_account_id", "resource_id", "finding_type", "recommendation_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_recommendations_reconcile_key", table_name="recommendations")
    op.drop_column("recommendations", "resolution_source")
    op.drop_column("recommendations", "resolved_at")
    op.drop_column("recommendations", "dismissed_by")
    op.drop_column("recommendations", "dismissed_at")
    op.drop_column("recommendations", "dismissed_reason_note")
    op.drop_column("recommendations", "dismissed_reason")
    op.drop_column("recommendations", "snoozed_by")
    op.drop_column("recommendations", "snoozed_until")
    op.drop_column("recommendations", "state")
    op.drop_column("recommendations", "finding_type")
