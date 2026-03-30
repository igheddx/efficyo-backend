"""Add findings.sync_run_id to correlate findings with a sync job or detect run.

Revision ID: 017
Revises: 016
Create Date: 2026-03-28 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "findings",
        sa.Column("sync_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_findings_sync_run_id", "findings", ["sync_run_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_findings_sync_run_id", table_name="findings")
    op.drop_column("findings", "sync_run_id")
