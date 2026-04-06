"""049 — Phase 4 notification tables: policies, schedules, snoozes + delivery log retry columns."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "049"
down_revision = "048_user_notification_targeting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── notification_policies ────────────────────────────────────────────────
    op.create_table(
        "notification_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("min_priority", sa.String(16), nullable=False, server_default="low"),
        sa.Column("enabled_event_types", postgresql.JSONB(), nullable=True),
        sa.Column("throttle_window_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("max_per_window", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("digest_mode", sa.String(16), nullable=False, server_default="instant"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", name="uq_notification_policy_org"),
    )
    op.create_index("ix_notification_policies_org", "notification_policies", ["organization_id"])

    # ── notification_schedules ───────────────────────────────────────────────
    op.create_table(
        "notification_schedules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("frequency", sa.String(16), nullable=False, server_default="daily"),
        sa.Column("day_of_week", sa.Integer(), nullable=True),
        sa.Column("time_of_day", sa.String(5), nullable=False, server_default="09:00"),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", name="uq_notification_schedule_org"),
    )
    op.create_index("ix_notification_schedules_org", "notification_schedules", ["organization_id"])
    op.create_index("ix_notification_schedules_next_run", "notification_schedules", ["next_run_at"])

    # ── notification_snoozes ─────────────────────────────────────────────────
    op.create_table(
        "notification_snoozes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("entity_key", sa.String(256), nullable=False),
        sa.Column("snooze_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_notification_snoozes_org", "notification_snoozes", ["organization_id"])
    op.create_index("ix_notification_snoozes_entity", "notification_snoozes", ["entity_key"])
    op.create_index("ix_notification_snoozes_until", "notification_snoozes", ["snooze_until"])
    op.create_index(
        "ix_notification_snooze_lookup",
        "notification_snoozes",
        ["organization_id", "entity_key", "snooze_until"],
    )

    # ── notification_delivery_logs — add Phase 4 retry columns ───────────────
    op.add_column(
        "notification_delivery_logs",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "notification_delivery_logs",
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "notification_delivery_logs",
        sa.Column("provider_response", postgresql.JSONB(), nullable=True),
    )
    op.create_index(
        "ix_notification_delivery_logs_next_retry",
        "notification_delivery_logs",
        ["next_retry_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_notification_delivery_logs_next_retry", "notification_delivery_logs")
    op.drop_column("notification_delivery_logs", "provider_response")
    op.drop_column("notification_delivery_logs", "next_retry_at")
    op.drop_column("notification_delivery_logs", "retry_count")

    op.drop_table("notification_snoozes")
    op.drop_table("notification_schedules")
    op.drop_table("notification_policies")
