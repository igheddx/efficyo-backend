#!/usr/bin/env python3
"""
Delete all cloud accounts (and all their ingested data) for a given tenant,
then reset the user's saved context defaults so they start fresh.

Usage — from the backend directory with venv active:

  python scripts/reset_tenant_cloud_accounts.py --tenant-name "TIPWAVE TECHNOLOGIES, LLC"

Or by tenant UUID:

  python scripts/reset_tenant_cloud_accounts.py --tenant-id <UUID>

After this, re-onboard via the CloudFormation wizard.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from uuid import UUID

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = val


_load_env_file(_BACKEND_ROOT / ".env")

# Must import after env is loaded.
from app.core.db import SessionLocal  # noqa: E402
from app.models.cloud_account import CloudAccount  # noqa: E402
from app.models.tenant import Tenant  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.cloud_account_reset_service import clear_ingested_data_for_cloud_account  # noqa: E402


def _find_tenant(db, *, tenant_id: str | None, tenant_name: str | None) -> Tenant:
    if tenant_id:
        t = db.query(Tenant).filter(Tenant.id == UUID(tenant_id)).first()
        if t is None:
            raise SystemExit(f"No tenant with id={tenant_id}")
        return t
    t = db.query(Tenant).filter(Tenant.name == tenant_name).first()
    if t is None:
        # Try case-insensitive
        t = db.query(Tenant).filter(Tenant.name.ilike(tenant_name)).first()
    if t is None:
        all_names = [r.name for r in db.query(Tenant.name).all()]
        raise SystemExit(f"No tenant named '{tenant_name}'. Available: {all_names}")
    return t


def main() -> None:
    ap = argparse.ArgumentParser(description="Delete all cloud accounts for a tenant (fresh re-onboard).")
    ap.add_argument("--tenant-id", help="Tenant UUID")
    ap.add_argument("--tenant-name", help="Tenant name (case-insensitive)")
    ap.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    args = ap.parse_args()

    if not args.tenant_id and not args.tenant_name:
        ap.error("Provide --tenant-id or --tenant-name")

    db = SessionLocal()
    try:
        tenant = _find_tenant(db, tenant_id=args.tenant_id, tenant_name=args.tenant_name)
        accounts = (
            db.query(CloudAccount)
            .filter(CloudAccount.tenant_id == tenant.id)
            .order_by(CloudAccount.name.asc())
            .all()
        )

        if not accounts:
            print(f"Tenant '{tenant.name}' has no cloud accounts. Nothing to do.")
            return

        print(f"\nTenant: {tenant.name} ({tenant.id})")
        print(f"Found {len(accounts)} cloud account(s):")
        for ca in accounts:
            print(f"  • {ca.name!r}  AWS={ca.account_id}  status={ca.status}  id={ca.id}")

        if not args.yes:
            ans = input("\nDelete ALL of the above accounts and their data? [yes/N] ").strip().lower()
            if ans != "yes":
                print("Aborted.")
                return

        for ca in accounts:
            print(f"\nClearing ingested data for {ca.name!r} ({ca.account_id})...")
            try:
                deleted = clear_ingested_data_for_cloud_account(db, tenant.id, ca.id)
                for k, v in deleted.items():
                    if v:
                        print(f"  deleted {v} {k}")
            except Exception as exc:
                print(f"  Warning — could not clear data: {exc}")

            print(f"  Deleting cloud_accounts row {ca.id}...")
            db.delete(ca)

        db.commit()
        print("\nCloud account rows deleted.")

        # Reset saved context defaults for all users pointing at this tenant.
        users_updated = 0
        users = (
            db.query(User)
            .filter(User.default_tenant_id == tenant.id)
            .all()
        )
        for user in users:
            user.default_tenant_id = None
            user.default_cloud_account_id = None
            db.add(user)
            users_updated += 1
        if users_updated:
            db.commit()
            print(f"Reset saved context defaults for {users_updated} user(s).")

        print("\nDone. Re-onboard via the Admin → AWS Connect flow.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
