"""Add user notification destination mapping, delivery tracing, and ack fields."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "048_user_notification_targeting"
down_revision = "047_org_integration_telegram"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_notification_destinations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slack_user_id", sa.String(length=128), nullable=True),
        sa.Column("teams_user_identifier", sa.String(length=256), nullable=True),
        sa.Column("telegram_chat_id", sa.String(length=64), nullable=True),
        sa.Column("notifications_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("receive_direct_notifications", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("receive_approvals", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("receive_failures", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("quiet_hours_start", sa.String(length=5), nullable=True),
        sa.Column("quiet_hours_end", sa.String(length=5), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "organization_id", name="uq_user_notification_dest_user_org"),
    )
    op.create_index(op.f("ix_user_notification_destinations_user_id"), "user_notification_destinations", ["user_id"], unique=False)
    op.create_index(op.f("ix_user_notification_destinations_organization_id"), "user_notification_destinations", ["organization_id"], unique=False)

    op.create_table(
        "notification_delivery_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("notification_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("notifications.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_key", sa.String(length=256), nullable=False),
        sa.Column("route_kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("dedupe_key", sa.String(length=200), nullable=False),
        sa.Column("rate_limited", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "provider",
            "target_type",
            "target_key",
            "dedupe_key",
            name="uq_notification_delivery_provider_target_dedupe",
        ),
    )
    op.create_index(op.f("ix_notification_delivery_logs_organization_id"), "notification_delivery_logs", ["organization_id"], unique=False)
    op.create_index(op.f("ix_notification_delivery_logs_user_id"), "notification_delivery_logs", ["user_id"], unique=False)
    op.create_index(op.f("ix_notification_delivery_logs_notification_id"), "notification_delivery_logs", ["notification_id"], unique=False)
    op.create_index(op.f("ix_notification_delivery_logs_event_type"), "notification_delivery_logs", ["event_type"], unique=False)
    op.create_index(op.f("ix_notification_delivery_logs_provider"), "notification_delivery_logs", ["provider"], unique=False)
    op.create_index(op.f("ix_notification_delivery_logs_dedupe_key"), "notification_delivery_logs", ["dedupe_key"], unique=False)

    op.add_column("notifications", sa.Column("read_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("notifications", sa.Column("is_acknowledged", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("notifications", sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_notifications_is_acknowledged"), "notifications", ["is_acknowledged"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_notifications_is_acknowledged"), table_name="notifications")
    op.drop_column("notifications", "acknowledged_at")
    op.drop_column("notifications", "is_acknowledged")
    op.drop_column("notifications", "read_at")

    op.drop_index(op.f("ix_notification_delivery_logs_dedupe_key"), table_name="notification_delivery_logs")
    op.drop_index(op.f("ix_notification_delivery_logs_provider"), table_name="notification_delivery_logs")
    op.drop_index(op.f("ix_notification_delivery_logs_event_type"), table_name="notification_delivery_logs")
    op.drop_index(op.f("ix_notification_delivery_logs_notification_id"), table_name="notification_delivery_logs")
    op.drop_index(op.f("ix_notification_delivery_logs_user_id"), table_name="notification_delivery_logs")
    op.drop_index(op.f("ix_notification_delivery_logs_organization_id"), table_name="notification_delivery_logs")
    op.drop_table("notification_delivery_logs")

    op.drop_index(op.f("ix_user_notification_destinations_organization_id"), table_name="user_notification_destinations")
    op.drop_index(op.f("ix_user_notification_destinations_user_id"), table_name="user_notification_destinations")
    op.drop_table("user_notification_destinations")
