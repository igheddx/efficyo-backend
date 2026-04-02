# MEEZI Operations Runbook

## Purpose

This runbook covers the operational baseline for the MEEZI test and production environments after the observability and hardening update.

It documents:

- health visibility
- log locations
- backup behavior
- cron jobs
- deployment validation
- routine operator checks

## Environment Summary

| Environment | Frontend | API | Branch | Host |
|---|---|---|---|---|
| test | https://apptest.meezi.io | https://apptest-api.meezi.io | develop | meezi-test |
| production | https://app.meezi.io | https://api.meezi.io | main | meezi-prod |

## Runtime Layout

Each backend VM uses the same structure:

- `/opt/meezi/docker-compose.yml`
- `/opt/meezi/.env.backend`
- `/opt/meezi/.env.db`
- `/opt/meezi/runtime/`
- `/var/log/meezi/`
- `/var/backups/meezi/`

Container mounts introduced by the hardening change:

- `/var/log/meezi:/var/log/meezi`
- `/opt/meezi/runtime:/app/runtime`

## Health Model

The API health endpoint is `GET /health`.

Expected response shape:

```json
{
  "status": "ok",
  "database": "ok",
  "worker": "ok",
  "worker_last_seen": "2026-04-02T12:34:56.000000+00:00"
}
```

Health behavior:

- `database=ok` means the API can execute `SELECT 1` against PostgreSQL.
- `worker=ok` means the worker heartbeat file is present and fresh.
- HTTP `503` means the API or worker is degraded.
- The worker heartbeat file is `/opt/meezi/runtime/worker-heartbeat.json` on the host.

Validation commands:

```bash
curl -fsS https://apptest-api.meezi.io/health | jq
curl -fsS https://api.meezi.io/health | jq
```

## Logging

All application and nginx logs are standardized under `/var/log/meezi`.

Primary files:

- `/var/log/meezi/api.log`
- `/var/log/meezi/worker.log`
- `/var/log/meezi/nginx-access.log`
- `/var/log/meezi/nginx-error.log`
- `/var/log/meezi/cron.log`

Useful commands:

```bash
tail -f /var/log/meezi/api.log
tail -f /var/log/meezi/worker.log
tail -f /var/log/meezi/nginx-error.log
tail -f /var/log/meezi/cron.log
grep -iE 'error|traceback|failed' /var/log/meezi/*.log
```

Log rotation:

- managed by `/etc/logrotate.d/meezi`
- daily rotation
- `copytruncate` enabled for container-written logs
- retention: 7 days on test, 14 days on production

## Cron Jobs

The checked-in scripts live under:

- `deploy/meezi-test/jobs`
- `deploy/meezi-prod/jobs`

Expected scheduled jobs:

| Job | Purpose | Suggested Schedule |
|---|---|---|
| `health-check.sh` | external health check for API and frontend | every 6 hours |
| `cost-sync.sh` | cost sync execution | daily |
| `resource-sync.sh` | resource sync enqueue | every 12 hours |
| `backup-db.sh` | compressed PostgreSQL dump | daily |
| `ops-check.sh` | disk and container status snapshot | daily |
| `docker-prune.sh` | image/builder cleanup | weekly |

Example crontab for test:

```cron
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

5 */6 * * * /opt/meezi/jobs/health-check.sh
20 2 * * * /opt/meezi/jobs/cost-sync.sh
35 3,15 * * * /opt/meezi/jobs/resource-sync.sh
50 2 * * * /opt/meezi/jobs/backup-db.sh
10 4 * * * /opt/meezi/jobs/ops-check.sh
30 4 * * 0 /opt/meezi/jobs/docker-prune.sh
```

## Backups

Database backups are stored under `/var/backups/meezi`.

File naming:

- test: `test-YYYYMMDD-HHMMSS.dump`
- production: `prod-YYYYMMDD-HHMMSS.dump`

Retention:

- test: 7 days
- production: 30 days

Manual backup:

```bash
sudo /opt/meezi/jobs/backup-db.sh
ls -lh /var/backups/meezi
```

Restore example:

```bash
createdb -U optimizer restore_db
pg_restore -U optimizer -d restore_db /var/backups/meezi/prod-YYYYMMDD-HHMMSS.dump
```

## Deploy Validation

After a backend deploy, verify the following in order:

1. `docker compose ps` shows `db`, `api`, and `worker` as running.
2. `curl -fsS https://<env-api>/health` returns HTTP 200.
3. `/opt/meezi/runtime/worker-heartbeat.json` updates every few seconds.
4. `/var/log/meezi/api.log` and `/var/log/meezi/worker.log` are being written.
5. nginx access and error logs are present.
6. `sudo nginx -t` passes after nginx config updates.
7. a manual `backup-db.sh` run creates a dump file.

Core commands:

```bash
cd /opt/meezi && sudo docker compose ps
sudo docker compose -f /opt/meezi/docker-compose.yml logs --tail=100 api
sudo docker compose -f /opt/meezi/docker-compose.yml logs --tail=100 worker
sudo cat /opt/meezi/runtime/worker-heartbeat.json
sudo nginx -t
```

## Routine Checks

Daily:

- confirm `/health` returns `200`
- scan `/var/log/meezi/cron.log` for failed jobs
- verify latest backup timestamp in `/var/backups/meezi`

Weekly:

- review disk usage
- review Docker image growth
- sample nginx error log volume

Monthly:

- restore-test a recent backup on a scratch database
- review cron schedules against platform load

## Incident Notes

If `/health` reports `worker=stale`:

```bash
cd /opt/meezi
sudo docker compose logs --tail=200 worker
sudo docker compose restart worker
sudo cat /opt/meezi/runtime/worker-heartbeat.json
```

If `/health` reports `database=error`:

```bash
cd /opt/meezi
sudo docker compose ps
sudo docker compose logs --tail=200 db
sudo docker compose restart db api worker
```

If logs stop rotating:

```bash
sudo logrotate -f /etc/logrotate.d/meezi
ls -lh /var/log/meezi
```

## Release Workflow Reminder

Required promotion path:

1. develop changes are prepared on a feature branch
2. feature branch is merged into `develop`
3. test environment is deployed and validated from `develop`
4. production merge into `main` happens only after explicit approval