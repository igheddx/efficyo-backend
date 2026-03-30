#!/usr/bin/env python3
"""
Delete all ingestion-derived data for one tenant + cloud account, then exit.

Loads ``backend/.env`` if present (does not override other variables already set in the shell).
If ``DATABASE_URL`` is set to a doc placeholder (host ``...``), it is ignored so ``.env`` can supply a real URL.

Usage — from repo root:

  python3 scripts/clear_cloud_account_ingestion.py --tenant-id UUID --cloud-account-id UUID

Or:

  cd backend && python scripts/clear_cloud_account_ingestion.py --tenant-id UUID --cloud-account-id UUID

After this, trigger a full sync from the UI or POST .../sync.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from uuid import UUID

# Parent of `scripts/` is the backend package root (`.../backend`), where `app` lives.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


def _discard_placeholder_database_url() -> None:
    """Remove DATABASE_URL if it still contains the literal ``...@...`` host from copy-paste."""
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        return
    if url in ("postgresql+psycopg2://...", "...", "postgres://..."):
        del os.environ["DATABASE_URL"]
        return
    if re.search(r"@\.\.\.([:/]|$)", url):
        del os.environ["DATABASE_URL"]


def _load_env_file(path: Path) -> None:
    """KEY=VALUE lines from .env; skip comments; optional ``export `` prefix; never override os.environ."""
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
    parser = argparse.ArgumentParser(description="Clear ingestion data for a cloud account")
    parser.add_argument("--tenant-id", type=UUID, required=True)
    parser.add_argument("--cloud-account-id", type=UUID, required=True)
    args = parser.parse_args()

    _discard_placeholder_database_url()
    _load_env_file(_BACKEND_ROOT / ".env")

    from sqlalchemy.exc import OperationalError

    from app.core.db import SessionLocal
    from app.services.cloud_account_reset_service import clear_ingested_data_for_cloud_account

    db = None
    try:
        db = SessionLocal()
        deleted = clear_ingested_data_for_cloud_account(db, args.tenant_id, args.cloud_account_id)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OperationalError as exc:
        print(
            "Could not connect to Postgres. If you once exported DATABASE_URL=postgresql+psycopg2://... "
            "(literal dots), run: unset DATABASE_URL\n"
            "Otherwise fix DATABASE_URL in backend/.env and ensure Postgres is running.\n",
            str(exc),
            file=sys.stderr,
            sep="\n",
        )
        return 1
    finally:
        if db is not None:
            db.close()

    print(json.dumps({"deleted": deleted}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
