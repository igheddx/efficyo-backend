"""Enforce unique reconcile key on recommendations.

Deduplicates existing rows (keeping most-recent by created_at then id),
drops the non-unique ix_recommendations_reconcile_key index added in 040,
then creates a proper unique constraint on the same five columns.

Revision ID: 041_reconcile_key_unique
Revises: 040_recommendation_lifecycle_state
Create Date: 2026-04-03
"""

from alembic import op


revision = "041_reconcile_key_unique"
down_revision = "040_lifecycle_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Remove duplicate rows — keep the most recently created row per
    # (tenant_id, cloud_account_id, resource_id, finding_type, recommendation_type).
    # This mirrors the window-function ordering already used by
    # _upsert_recommendations_for_findings() in the service layer.
    op.execute(
        """
        DELETE FROM recommendations
        WHERE id IN (
            SELECT id FROM (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY
                            tenant_id,
                            cloud_account_id,
                            resource_id,
                            finding_type,
                            recommendation_type
                        ORDER BY created_at DESC NULLS LAST, id DESC
                    ) AS rn
                FROM recommendations
            ) ranked
            WHERE rn > 1
        )
        """
    )

    # Replace the non-unique index from migration 040 with a unique one.
    op.drop_index("ix_recommendations_reconcile_key", table_name="recommendations")
    op.create_unique_constraint(
        "uq_recommendations_reconcile_key",
        "recommendations",
        ["tenant_id", "cloud_account_id", "resource_id", "finding_type", "recommendation_type"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_recommendations_reconcile_key", "recommendations", type_="unique")
    op.create_index(
        "ix_recommendations_reconcile_key",
        "recommendations",
        ["tenant_id", "cloud_account_id", "resource_id", "finding_type", "recommendation_type"],
        unique=False,
    )
