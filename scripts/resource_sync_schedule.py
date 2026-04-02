#!/usr/bin/env python3
"""
Conservative scheduled resource sync enqueuer.

This script only enqueues sync jobs into the DB-backed queue. Actual task
execution is handled by the long-running sync worker process.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import UUID


_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))
_LOCK_PATH = Path("/tmp/fptnext-resource-sync.lock")


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
    # Explicit imports avoid lazy mapper resolution issues.
    from app.models.access_grant import AccessGrant  # noqa: F401
    from app.models.approval_request import ApprovalAssignment, ApprovalRequest  # noqa: F401
    from app.models.cloud_account import CloudAccount  # noqa: F401
    from app.models.account_tag_key import AccountTagKey  # noqa: F401
    from app.models.cost_snapshot import CostApiUsageLog, CostFetchLock, CostSnapshot, CostSyncPolicy  # noqa: F401
    from app.models.execution_audit_event import ExecutionAuditEvent  # noqa: F401
    from app.models.execution_policy import ExecutionPolicy  # noqa: F401
    from app.models.execution_owner import ExecutionOwnerAssignment  # noqa: F401
    from app.models.finding import Finding  # noqa: F401
    from app.models.ingestion_job import IngestionJob  # noqa: F401
    from app.models.notification import Notification  # noqa: F401
    from app.models.organization import Organization  # noqa: F401
    from app.models.policy_profile import PolicyProfile  # noqa: F401
    from app.models.recommendation import Recommendation  # noqa: F401
    from app.models.recommendation_outcome import RecommendationOutcome  # noqa: F401
    from app.models.resource_snapshot import ResourceSnapshot  # noqa: F401
    from app.models.sync_pipeline import SyncJob, SyncJobEvent, SyncTask  # noqa: F401
    from app.models.tenant import Tenant  # noqa: F401
    from app.models.user import AuthSession, User  # noqa: F401


def main() -> int:
    parser = argparse.ArgumentParser(description="Enqueue conservative scheduled resource sync jobs")
    parser.add_argument("--tenant-id", type=UUID, default=None, help="Optional: scope to one tenant")
    parser.add_argument("--cloud-account-id", type=UUID, default=None, help="Optional: scope to one cloud account")
    parser.add_argument("--limit", type=int, default=0, help="Max accounts to enqueue (0=no limit)")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be enqueued without DB writes")
    args = parser.parse_args()

    if args.cloud_account_id is not None and args.tenant_id is None:
        print("When --cloud-account-id is set, --tenant-id is required.", file=sys.stderr)
        return 2

    _load_env_file(_BACKEND_ROOT / ".env")
    _ensure_orm_models_loaded()

    lock_fd: int | None = None
    try:
        lock_fd = os.open(str(_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.write(lock_fd, f"{os.getpid()}\n".encode("utf-8"))
    except FileExistsError:
        print(json.dumps({"status": "skipped", "reason": "lock_exists", "lock_path": str(_LOCK_PATH)}))
        return 0

    from app.core.db import SessionLocal
    from app.models.cloud_account import CloudAccount
    from app.sync import orchestrator, repository
    from app.sync.queue.database import DatabaseTaskQueue

    db = SessionLocal()
    out: dict[str, object] = {
        "processed": 0,
        "enqueued": 0,
        "skipped_active": 0,
        "failed": 0,
        "dry_run": bool(args.dry_run),
        "items": [],
    }
    try:
        q = db.query(CloudAccount).order_by(CloudAccount.created_at.asc())
        if args.tenant_id is not None:
            q = q.filter(CloudAccount.tenant_id == args.tenant_id)
        if args.cloud_account_id is not None:
            q = q.filter(CloudAccount.id == args.cloud_account_id)

        rows = q.all()
        if args.limit and args.limit > 0:
            rows = rows[: args.limit]

        queue = DatabaseTaskQueue()
        for ca in rows:
            out["processed"] = int(out["processed"]) + 1
            item = {
                "tenant_id": str(ca.tenant_id),
                "cloud_account_id": str(ca.id),
            }
            try:
                active = repository.count_active_jobs_for_scope(
                    db,
                    tenant_id=ca.tenant_id,
                    cloud_account_id=ca.id,
                    provider="aws",
                )
                if active > 0:
                    out["skipped_active"] = int(out["skipped_active"]) + 1
                    item["status"] = "skipped_active"
                    item["active_jobs"] = active
                    out["items"].append(item)
                    continue

                org_id = ca.tenant.organization_id
                if org_id is None:
                    out["failed"] = int(out["failed"]) + 1
                    item["status"] = "failed"
                    item["error"] = "tenant_missing_organization"
                    out["items"].append(item)
                    continue

                if args.dry_run:
                    out["enqueued"] = int(out["enqueued"]) + 1
                    item["status"] = "would_enqueue"
                    out["items"].append(item)
                    continue

                job = orchestrator.create_job_with_plan(
                    db,
                    organization_id=org_id,
                    tenant_id=ca.tenant_id,
                    cloud_account_id=ca.id,
                    provider="aws",
                    initiated_by_user_id=None,
                    trigger_type="scheduled",
                    force_new=False,
                    queue=queue,
                )
                db.commit()
                out["enqueued"] = int(out["enqueued"]) + 1
                item["status"] = "enqueued"
                item["job_id"] = str(job.id)
            except Exception as exc:
                db.rollback()
                out["failed"] = int(out["failed"]) + 1
                item["status"] = "failed"
                item["error"] = str(exc)[:500]
            out["items"].append(item)

        print(json.dumps(out, ensure_ascii=True, indent=2, sort_keys=True))
        return 1 if int(out["failed"]) > 0 else 0
    finally:
        try:
            db.close()
        finally:
            if lock_fd is not None:
                try:
                    os.close(lock_fd)
                except OSError:
                    pass
                try:
                    _LOCK_PATH.unlink(missing_ok=True)
                except OSError:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
