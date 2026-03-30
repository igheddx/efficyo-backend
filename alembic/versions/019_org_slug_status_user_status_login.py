"""Organization slug/status; user status and last_login_at.

Revision ID: 019
Revises: 018
Create Date: 2026-03-27 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("slug", sa.String(length=255), nullable=True))
    op.add_column(
        "organizations",
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
    )

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, name FROM organizations")).fetchall()
    for row in rows:
        oid, _name = row[0], row[1]
        slug = f"org-{str(oid).replace('-', '')}"
        conn.execute(
            sa.text("UPDATE organizations SET slug = :slug WHERE id = :id"),
            {"slug": slug[:255], "id": oid},
        )

    op.alter_column("organizations", "slug", nullable=False)
    op.create_index("ix_organizations_slug", "organizations", ["slug"], unique=True)

    op.add_column(
        "users",
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
    )
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_index("ix_organizations_slug", table_name="organizations")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "status")
    op.drop_column("organizations", "status")
    op.drop_column("organizations", "slug")
