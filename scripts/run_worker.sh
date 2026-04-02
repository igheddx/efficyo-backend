#!/bin/sh
set -eu

mkdir -p /var/log/meezi /app/runtime

exec python /app/worker_entrypoint.py >> /var/log/meezi/worker.log 2>&1