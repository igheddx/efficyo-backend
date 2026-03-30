"""Add recommendation_category to recommendations.

Revision ID: 008
Revises: 007
Create Date: 2026-03-24 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recommendations",
        sa.Column("recommendation_category", sa.String(length=20), nullable=False, server_default="governance"),
    )

    op.execute(
        sa.text(
            """
            UPDATE recommendations
            SET recommendation_category = CASE
                WHEN recommendation_type IN (
                    'aurora_serverless_cost_review',
                    'lambda_rightsize_memory',
                    's3_add_lifecycle_policy'
                ) THEN 'cost'
                WHEN recommendation_type IN (
                    'rds_disable_public_access',
                    's3_enable_public_access_block',
                    's3_enable_versioning'
                ) THEN 'security'
                ELSE 'governance'
            END
            """
        )
    )

    op.alter_column("recommendations", "recommendation_category", server_default=None)


def downgrade() -> None:
    op.drop_column("recommendations", "recommendation_category")