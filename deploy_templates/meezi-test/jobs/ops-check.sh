#!/bin/bash
set -euo pipefail

LOG_FILE="/var/log/meezi/cron.log"

{
  echo "=== Ops Check at $(date) ==="
  df -h /
  df -h /var/log || true
  df -h /var/backups || true
  docker ps --format 'table {{.Names}}\t{{.Status}}'
} >> "$LOG_FILE" 2>&1