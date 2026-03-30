#!/usr/bin/env python3
"""
Remove approval workflow rows and related notifications from Postgres.

Also optionally reset all ``recommendation_outcomes`` to ``workflow_status=suggested`` so you can use
**Submit for approval** again (the API only allows submit when workflow is ``suggested`` or ``rejected``).

For a **full** re-ingestion (delete findings, recommendations, snapshots, outcomes, then re-run sync), use:

    python3 scripts/clear_cloud_account_ingestion.py --tenant-id UUID --cloud-account-id UUID

Loads ``backend/.env`` if present (same behavior as ``clear_cloud_account_ingestion.py``).

Usage — from repo root:

    python3 backend/scripts/clear_approval_data.py
    python3 backend/scripts/clear_approval_data.py --tenant-id UUID --cloud-account-id UUID
    python3 backend/scripts/clear_approval_data.py --reset-outcomes

Or:

    cd backend && python scripts/clear_approval_data.py --reset-outcomes
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from uuid import UUID

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


def _discard_placeholder_database_url() -> None:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        return
    if url in ("postgresql+psycopg2://...", "...", "postgres://..."):
        del os.environ["DATABASE_URL"]
        return
    if re.search(r"@\.\.\.([:/]|$)", url):
        del os.environ["DATABASE_URL"]


def _ensure_orm_models_loaded() -> None:
    """Import model modules so SQLAlchemy can resolve relationships before queries."""
    from app.models.access_grant import AccessGrant  # noqa: F401
    from app.models.approval_request import ApprovalAssignment, ApprovalRequest  # noqa: F401
    from app.models.cloud_account import CloudAccount  # noqa: F401
    from app.models.execution_audit_event import ExecutionAuditEvent  # noqa: F401
    from app.models.execution_policy import ExecutionPolicy  # noqa: F401
    from app.models.finding import Finding  # noqa: F401
    from app.models.ingestion_job import IngestionJob  # noqa: F401
    from app.models.notification import Notification  # noqa: F401
    from app.models.organization import Organization  # noqa: F401
    from app.models.policy_profile import PolicyProfile  # noqa: F401
    from app.models.recommendation import Recommendation  # noqa: F401
    from app.models.recommendation_outcome import RecommendationOutcome  # noqa: F401
    from app.models.resource_snapshot import ResourceSnapshot  # noqa: F401
    from app.models.tenant import Tenant  # noqa: F401
    from app.models.user import AuthSession, User  # noqa: F401


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Clear approval_requests / assignments and related notifications")
    parser.add_argument("--tenant-id", type=UUID, default=None, help="Limit to this tenant (with --cloud-account-id)")
    parser.add_argument(
        "--cloud-account-id",
        type=UUID,
        default=None,
        help="Limit to this cloud account (with --tenant-id)",
    )
    parser.add_argument(
        "--reset-outcomes",
        action="store_true",
        help="Reset recommendation_outcomes to suggested/pending and clear workflow + savings fields "
        "(needed if workflow had moved past suggested). Scoped to --tenant-id/--cloud-account-id when both set; "
        "otherwise resets outcomes for all tenants (dev only).",
    )
    args = parser.parse_args()

    if (args.tenant_id is None) ^ (args.cloud_account_id is None):
        print("Either pass both --tenant-id and --cloud-account-id, or neither.", file=sys.stderr)
        return 1

    _discard_placeholder_database_url()
    _load_env_file(_BACKEND_ROOT / ".env")

    from sqlalchemy.exc import InvalidRequestError, OperationalError

    _ensure_orm_models_loaded()
    from app.core.db import SessionLocal
    from app.services.approval_reset_service import (
        clear_approval_data,
        reset_recommendation_outcome_workflow_for_resubmit,
    )

    db = None
    try:
        db = SessionLocal()
        counts = clear_approval_data(
            db,
            tenant_id=args.tenant_id,
            cloud_account_id=args.cloud_account_id,
        )
        out: dict = {"cleared": counts}
        if args.reset_outcomes:
            out["recommendation_outcomes_reset"] = reset_recommendation_outcome_workflow_for_resubmit(
                db,
                tenant_id=args.tenant_id,
                cloud_account_id=args.cloud_account_id,
            )
        else:
            out["recommendation_outcomes_reset"] = None
        print(json.dumps(out, indent=2, default=str))
    except OperationalError as exc:
        print(
            "Could not connect to Postgres. Fix DATABASE_URL in backend/.env and ensure Postgres is running.\n",
            str(exc),
            file=sys.stderr,
            sep="\n",
        )
        return 1
    except InvalidRequestError as exc:
        print(f"ORM configuration error: {exc}", file=sys.stderr)
        return 1
    finally:
        if db is not None:
            db.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
