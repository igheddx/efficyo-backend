#!/usr/bin/env python3
"""
Daily cost snapshot sync runner.

Intended for cron/job scheduler execution. This script performs controlled
cost syncs and never serves UI requests directly.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from uuid import UUID


_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))
_LOCK_PATH = Path("/tmp/fptnext-cost-sync.lock")
logger = logging.getLogger(__name__)


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
    # Core models
    from app.models.access_grant import AccessGrant  # noqa: F401
    from app.models.approval_request import ApprovalAssignment, ApprovalRequest  # noqa: F401
    from app.models.cloud_account import CloudAccount  # noqa: F401
    from app.models.account_tag_key import AccountTagKey  # noqa: F401
    from app.models.cost_snapshot import CostApiUsageLog, CostSnapshot, CostSyncPolicy  # noqa: F401
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


def _notify_cost_sync_alert(
    db,
    *,
    org_id,
    tenant_id,
    cloud_account_id,
    message: str,
    details: dict | None = None,
) -> None:
    """Best-effort alert to org admins + root admins."""
    from app.models.user import User
    from app.services import notification_service

    if org_id is None:
        return
    recipient_ids = set(
        notification_service.user_ids_for_org_roles(
            db,
            org_id,
            frozenset({"org_admin", "root_admin"}),
        )
    )
    for (uid,) in db.query(User.id).filter(User.is_root_admin.is_(True)).all():
        if uid is not None:
            recipient_ids.add(uid)
    notification_service.emit_to_users(
        db,
        organization_id=org_id,
        user_ids=list(recipient_ids),
        notification_type="cost_sync_alert",
        message=message[:500],
        entity_type="cloud_account" if cloud_account_id is not None else ("tenant" if tenant_id is not None else "organization"),
        entity_id=cloud_account_id or tenant_id or org_id,
        payload=details or {},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run daily cost snapshot sync for cloud accounts")
    parser.add_argument("--tenant-id", type=UUID, default=None, help="Optional: limit to one tenant")
    parser.add_argument("--cloud-account-id", type=UUID, default=None, help="Optional: limit to one cloud account")
    parser.add_argument("--force-refresh", action="store_true", help="Force live refresh even when snapshot is fresh")
    parser.add_argument("--limit", type=int, default=0, help="Optional: max number of accounts to process (0=no limit)")
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
        payload = {"status": "skipped", "reason": "lock_exists", "lock_path": str(_LOCK_PATH)}
        logger.warning("daily_cost_sync_skipped_lock_exists", extra=payload)
        print(json.dumps(payload, ensure_ascii=True))
        try:
            from app.core.db import SessionLocal
            from app.models.organization import Organization

            db_alert = SessionLocal()
            try:
                org_ids = [oid for (oid,) in db_alert.query(Organization.id).all()]
                for oid in org_ids:
                    _notify_cost_sync_alert(
                        db_alert,
                        org_id=oid,
                        tenant_id=None,
                        cloud_account_id=None,
                        message="Daily cost sync skipped: previous run lock is still active.",
                        details=payload,
                    )
            finally:
                db_alert.close()
        except Exception:
            logger.exception("Failed to emit lock-skip cost sync alerts")
        return 0

    from app.core.db import SessionLocal
    from app.models.cloud_account import CloudAccount
    from app.sync.jobs.cost_sync import CostSyncJobRunner
    from app.services import notification_service

    db = SessionLocal()
    out: dict = {"processed": 0, "succeeded": 0, "failed": 0, "items": []}
    try:
        q = db.query(CloudAccount).order_by(CloudAccount.created_at.asc())
        if args.tenant_id is not None:
            q = q.filter(CloudAccount.tenant_id == args.tenant_id)
        if args.cloud_account_id is not None:
            q = q.filter(CloudAccount.id == args.cloud_account_id)
        rows = q.all()
        if args.limit and args.limit > 0:
            rows = rows[: args.limit]

        runner = CostSyncJobRunner()
        for ca in rows:
            out["processed"] += 1
            item = {"tenant_id": str(ca.tenant_id), "cloud_account_id": str(ca.id)}
            org_id = notification_service.org_id_for_tenant(db, ca.tenant_id)
            try:
                result = runner.run(
                    db,
                    tenant_id=ca.tenant_id,
                    cloud_account_id=ca.id,
                    sync_job_id=None,
                    force_refresh=bool(args.force_refresh),
                )
                db.commit()
                out["succeeded"] += 1
                item["status"] = "ok"
                item["snapshot"] = result
            except Exception as exc:
                db.rollback()
                out["failed"] += 1
                item["status"] = "failed"
                item["error"] = str(exc)[:500]
                _notify_cost_sync_alert(
                    db,
                    org_id=org_id,
                    tenant_id=ca.tenant_id,
                    cloud_account_id=ca.id,
                    message=f"Daily cost sync failed for account {ca.id}: {str(exc)[:200]}",
                    details={
                        "tenant_id": str(ca.tenant_id),
                        "cloud_account_id": str(ca.id),
                        "error": str(exc)[:500],
                        "view": "dashboard",
                    },
                )
            out["items"].append(item)
    finally:
        db.close()
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except OSError:
                pass
            try:
                _LOCK_PATH.unlink(missing_ok=True)
            except OSError:
                pass

    if out["failed"] > 0:
        db2 = None
        try:
            db2 = SessionLocal()
            tenant_ids = sorted({UUID(item["tenant_id"]) for item in out["items"] if item.get("status") == "failed"})
            for tid in tenant_ids:
                org_id = notification_service.org_id_for_tenant(db2, tid)
                _notify_cost_sync_alert(
                    db2,
                    org_id=org_id,
                    tenant_id=tid,
                    cloud_account_id=None,
                    message=f"Daily cost sync completed with failures ({out['failed']} failed of {out['processed']}).",
                    details={"summary": {"processed": out["processed"], "failed": out["failed"]}, "view": "dashboard"},
                )
        except Exception:
            pass
        finally:
            try:
                if db2 is not None:
                    db2.close()
            except Exception:
                pass

    print(json.dumps(out, ensure_ascii=True, indent=2, sort_keys=True))
    return 1 if out["failed"] > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())

