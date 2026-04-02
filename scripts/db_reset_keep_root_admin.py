#!/usr/bin/env python3
"""
DESTRUCTIVE: Delete all application data and leave exactly one platform root user.

- Truncates every ORM table except ``alembic_version`` (schema migrations preserved).
- Creates (or recreates) ``admin@fptnext.local`` with ``is_root_admin=True``.

PostgreSQL only (uses TRUNCATE ... CASCADE). Run from repo root or backend with venv:

  cd backend && ../.venv/bin/python scripts/db_reset_keep_root_admin.py --i-understand

Password: set ``FPTNEXT_RESET_ADMIN_PASSWORD`` or pass ``--password`` (defaults to
``FPTNEXT_DEV_SEED_PASSWORD`` / ``devpassword`` for local dev).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        os.environ[key] = val


def _ensure_orm_models_loaded() -> None:
    """Import all models so ``Base.metadata`` is complete."""
    from app.models.access_grant import AccessGrant  # noqa: F401
    from app.models.account_tag_key import AccountTagKey  # noqa: F401
    from app.models.approval_request import ApprovalAssignment, ApprovalRequest  # noqa: F401
    from app.models.cloud_account import CloudAccount  # noqa: F401
    from app.models.cost_snapshot import CostApiUsageLog, CostFetchLock, CostSnapshot, CostSyncPolicy  # noqa: F401
    from app.models.execution_audit_event import ExecutionAuditEvent  # noqa: F401
    from app.models.execution_owner import ExecutionOwnerAssignment  # noqa: F401
    from app.models.execution_policy import ExecutionPolicy  # noqa: F401
    from app.models.finding import Finding  # noqa: F401
    from app.models.ingestion_job import IngestionJob  # noqa: F401
    from app.models.notification import Notification  # noqa: F401
    from app.models.organization import Organization, OrgMembership  # noqa: F401
    from app.models.policy_profile import PolicyProfile  # noqa: F401
    from app.models.recommendation import Recommendation  # noqa: F401
    from app.models.recommendation_outcome import RecommendationOutcome  # noqa: F401
    from app.models.resource_snapshot import ResourceSnapshot  # noqa: F401
    from app.models.sync_pipeline import SyncJob, SyncJobEvent, SyncTask  # noqa: F401
    from app.models.tenant import Tenant  # noqa: F401
    from app.models.user import AuthSession, User  # noqa: F401


def main() -> int:
    _load_env_file(_BACKEND_ROOT.parent / ".env")
    _load_env_file(_BACKEND_ROOT / ".env")

    parser = argparse.ArgumentParser(description="Wipe DB and keep a single root admin.")
    parser.add_argument(
        "--i-understand",
        action="store_true",
        help="Required. Confirms you accept permanent data loss.",
    )
    parser.add_argument(
        "--email",
        default="admin@fptnext.local",
        help="Root admin email (default: admin@fptnext.local)",
    )
    parser.add_argument(
        "--display-name",
        default="Root admin",
        help="Display name for the root user",
    )
    parser.add_argument(
        "--password",
        default="",
        help="Password for the root user (prefer FPTNEXT_RESET_ADMIN_PASSWORD in env)",
    )
    args = parser.parse_args()

    if not args.i_understand:
        print("Refusing to run without --i-understand (this deletes almost all database rows).", file=sys.stderr)
        return 2

    _ensure_orm_models_loaded()

    from sqlalchemy import inspect, text

    from app.core.config import settings
    from app.core.db import Base, SessionLocal, engine
    from app.services.auth_service import create_user

    dialect = inspect(engine).dialect.name
    if dialect != "postgresql":
        print(
            f"This script only supports PostgreSQL (got dialect {dialect!r}). "
            "Use a dump/restore or manual delete for SQLite.",
            file=sys.stderr,
        )
        return 3

    table_names = sorted(
        t.name for t in Base.metadata.sorted_tables if t.name and t.name != "alembic_version"
    )
    if not table_names:
        print("No tables in metadata — models not loaded?", file=sys.stderr)
        return 4

    quoted = ", ".join(f'"{n}"' for n in table_names)
    sql = text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE")

    with engine.begin() as conn:
        conn.execute(sql)

    password = (args.password or "").strip() or os.environ.get(
        "FPTNEXT_RESET_ADMIN_PASSWORD",
        os.environ.get("FPTNEXT_DEV_SEED_PASSWORD", settings.dev_seed_password),
    )

    db = SessionLocal()
    try:
        create_user(
            db,
            email=args.email.strip().lower(),
            password=password,
            display_name=args.display_name.strip() or args.email,
            is_root_admin=True,
        )
    finally:
        db.close()

    print(f"OK: truncated {len(table_names)} tables; created root admin {args.email!r}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
