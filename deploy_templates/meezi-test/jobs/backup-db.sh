#!/bin/bash
set -euo pipefail

BACKUP_DIR="/var/backups/meezi"
LOG_FILE="/var/log/meezi/cron.log"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT_FILE="$BACKUP_DIR/test-$STAMP.dump"

mkdir -p "$BACKUP_DIR"

{
  echo "=== DB Backup Started at $(date) ==="
  docker exec meezi-test-db pg_dump -U optimizer -d optimizer_db -Fc > "$OUT_FILE"
  find "$BACKUP_DIR" -type f -name 'test-*.dump' -mtime +7 -delete
  echo "DB Backup Completed at $(date): $OUT_FILE"
} >> "$LOG_FILE" 2>&1