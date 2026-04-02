#!/bin/bash
set -euo pipefail

LOG_FILE="/var/log/meezi/cron.log"
{
  echo "=== Cost Sync Started at $(date) ==="
  cd /opt/meezi
  docker compose exec -T api python scripts/cost_sync_daily.py
  echo "Cost Sync Completed at $(date)"
} >> "$LOG_FILE" 2>&1
