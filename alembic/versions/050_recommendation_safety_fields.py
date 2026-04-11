"""050 — Add safe_to_apply and caution_note to recommendations."""

from alembic import op
import sqlalchemy as sa

revision = "050_recommendation_safety_fields"
down_revision = "049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recommendations", sa.Column("safe_to_apply", sa.Boolean(), nullable=True))
    op.add_column("recommendations", sa.Column("caution_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("recommendations", "caution_note")
    op.drop_column("recommendations", "safe_to_apply")
