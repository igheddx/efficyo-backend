#!/bin/bash
set -euo pipefail

LOG_FILE="/var/log/meezi/cron.log"
{
  echo "=== Health Check at $(date) ==="
  curl -fsS https://api.meezi.io/health >/dev/null && echo "API healthy" || echo "API health check failed"
  curl -fsS https://app.meezi.io/ >/dev/null && echo "Frontend healthy" || echo "Frontend health check failed"
} >> "$LOG_FILE" 2>&1
