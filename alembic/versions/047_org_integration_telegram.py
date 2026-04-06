"""Add Telegram-specific fields (bot_token, chat_id) to org_integrations.

Teams reuses the existing webhook_url column — no new columns needed for Teams.
"""

from alembic import op
import sqlalchemy as sa

revision = "047_org_integration_telegram"
down_revision = "046_org_integrations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("org_integrations", sa.Column("bot_token", sa.Text(), nullable=True))
    op.add_column("org_integrations", sa.Column("chat_id", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("org_integrations", "chat_id")
    op.drop_column("org_integrations", "bot_token")
