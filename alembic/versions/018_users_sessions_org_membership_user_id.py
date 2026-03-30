"""Users, auth_sessions, org_memberships.user_id backfill.

Revision ID: 018
Revises: 017
Create Date: 2026-03-28 12:00:00.000000
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op
from passlib.hash import bcrypt
from sqlalchemy.dialects import postgresql


revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("is_root_admin", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("auth_provider", sa.String(length=64), nullable=False, server_default="local"),
        sa.Column("external_subject_id", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_external_subject_id", "users", ["external_subject_id"], unique=False)

    op.create_table(
        "auth_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["current_organization_id"], ["organizations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"], unique=False)
    op.create_index("ix_auth_sessions_token_hash", "auth_sessions", ["token_hash"], unique=True)
    op.create_index(
        "ix_auth_sessions_current_organization_id", "auth_sessions", ["current_organization_id"], unique=False
    )

    op.add_column(
        "org_memberships",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_org_memberships_user_id_users",
        "org_memberships",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_org_memberships_user_id", "org_memberships", ["user_id"], unique=False)

    conn = op.get_bind()
    now = datetime.now(timezone.utc)
    legacy_pw = bcrypt.hash("__legacy_no_password_login__")

    rows = conn.execute(sa.text("SELECT DISTINCT user_identifier FROM org_memberships")).fetchall()
    email_by_identifier: dict[str, str] = {}
    for (raw_id,) in rows:
        uid = str(raw_id).strip()
        email = uid if "@" in uid else f"{uid}@legacy.fptnext.local"
        email_by_identifier[uid] = email

    for uid, email in email_by_identifier.items():
        conn.execute(
            sa.text(
                """
                INSERT INTO users (id, email, password_hash, display_name, is_root_admin, auth_provider,
                    external_subject_id, created_at, updated_at)
                VALUES (:id, :email, :ph, :dn, false, 'local', NULL, :ts, :ts)
                ON CONFLICT (email) DO NOTHING
                """
            ),
            {
                "id": uuid.uuid4(),
                "email": email,
                "ph": legacy_pw,
                "dn": uid,
                "ts": now,
            },
        )

    memberships = conn.execute(sa.text("SELECT id, user_identifier FROM org_memberships")).fetchall()
    for mid, user_identifier in memberships:
        email = email_by_identifier.get(str(user_identifier).strip())
        if not email:
            continue
        urow = conn.execute(sa.text("SELECT id FROM users WHERE email = :e"), {"e": email}).fetchone()
        if urow:
            conn.execute(
                sa.text("UPDATE org_memberships SET user_id = :uid WHERE id = :mid"),
                {"uid": urow[0], "mid": mid},
            )


def downgrade() -> None:
    op.drop_index("ix_org_memberships_user_id", table_name="org_memberships")
    op.drop_constraint("fk_org_memberships_user_id_users", "org_memberships", type_="foreignkey")
    op.drop_column("org_memberships", "user_id")

    op.drop_index("ix_auth_sessions_current_organization_id", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_token_hash", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")

    op.drop_index("ix_users_external_subject_id", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
