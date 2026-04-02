#!/bin/bash
set -euo pipefail

LOG_FILE="/var/log/meezi/cron.log"
{
  echo "=== Resource Sync Started at $(date) ==="
  cd /opt/meezi
  docker compose exec -T api python scripts/resource_sync_schedule.py --limit 3
  echo "Resource Sync Completed at $(date)"
} >> "$LOG_FILE" 2>&1