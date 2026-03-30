"""Partial unique index: one active (queued/running) sync per tenant + cloud account.

Revision ID: 016
Revises: 015
Create Date: 2026-03-27 00:00:00.000000
"""

from alembic import op


revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_ingestion_jobs_active_scope
        ON ingestion_jobs (tenant_id, cloud_account_id)
        WHERE status IN ('queued', 'running')
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_ingestion_jobs_active_scope")
