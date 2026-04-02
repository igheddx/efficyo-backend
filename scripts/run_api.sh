#!/bin/sh
set -eu

mkdir -p /var/log/meezi /app/runtime

exec uvicorn app.main:app --host 0.0.0.0 --port 8000 >> /var/log/meezi/api.log 2>&1