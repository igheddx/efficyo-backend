"""Add optional ExternalId to cloud accounts for cross-account STS hardening.

Revision ID: 037_cloud_account_external_id
Revises: 036_user_invite_fields
Create Date: 2026-04-02
"""

from alembic import op
import sqlalchemy as sa

revision = "037_cloud_account_external_id"
down_revision = "036_user_invite_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cloud_accounts", sa.Column("external_id", sa.String(length=1224), nullable=True))


def downgrade() -> None:
    op.drop_column("cloud_accounts", "external_id")