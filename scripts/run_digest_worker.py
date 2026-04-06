#!/usr/bin/env python3
"""
Notification digest scheduler — standalone runner.

Fires due digest schedules, retries failed deliveries, and expires old snoozes.
Intended to be called from cron every 1–5 minutes.

    docker exec <api-container> python scripts/run_digest_worker.py

Or add to crontab:
    * * * * * /app/.venv/bin/python /app/scripts/run_digest_worker.py >> /var/log/digest_worker.log 2>&1
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.logging import configure_logging  # noqa: E402

configure_logging()
logger = logging.getLogger(__name__)


def main() -> int:
    from app.core.db import SessionLocal
    from app.workers.digest_scheduler import run_once

    db = SessionLocal()
    try:
        result = run_once(db)
        logger.info(
            "digest_worker: digests=%d retries=%d snoozes_expired=%d",
            result["digests"],
            result["retries"],
            result["snoozes_expired"],
        )
        return 0
    except Exception:
        logger.exception("digest_worker: unexpected error")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
