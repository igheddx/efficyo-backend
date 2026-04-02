#!/bin/bash
set -euo pipefail

LOG_FILE="/var/log/meezi/cron.log"

{
  echo "=== Docker Prune at $(date) ==="
  docker image prune -af --filter "until=168h"
  docker builder prune -af --filter "until=168h"
} >> "$LOG_FILE" 2>&1