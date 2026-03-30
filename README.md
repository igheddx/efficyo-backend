# Backend Service

This service is the initial backend foundation for an AWS autonomous cloud optimization platform.

It provides a minimal FastAPI application scaffold with:
- API version routing (`/api/v1`)
- health check endpoint (`/health`)
- environment-based configuration placeholders
- structured logging setup

No AWS logic, AI agent logic, or database wiring is implemented yet.

## Prerequisites

- Python 3.11+
- pip
- PostgreSQL reachable at `DATABASE_URL` (defaults in `app/core/config.py` match the `db` service in the repo root `docker-compose.yml`)

### Database

The API needs a running Postgres instance. Quickest option from the repo root (uses `docker-compose.yml` and `docker-compose.db.env` so `POSTGRES_PASSWORD` is always set):

```bash
docker compose up -d db
```

If you see “superuser password is not specified”, you are not starting the image with a non-empty `POSTGRES_PASSWORD` (for example you used `docker run postgres` without `-e`, or a compose file that omits it). Use the repo’s `docker compose` command above from the project root.

The compose file maps Postgres to **host port 5433** so it does not collide with a typical local Postgres on **5432**. If `alembic` reports `password authentication failed for user "optimizer"`, you are usually connecting to the wrong server (unset any `DATABASE_URL` that still points at `5432`, or set it explicitly to the URL in `backend/.env.example`).

Then apply migrations (from `backend/` with your venv active):

```bash
alembic upgrade head
```

If you use another host, port, user, or database, set `DATABASE_URL` before starting uvicorn (see `backend/.env.example`).

## Run Locally

1. Create and activate a virtual environment:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Start the API server:

   ```bash
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

4. Verify health check:

   ```bash
   curl http://localhost:8000/health
   ```

   Expected response:

   ```json
   {"status": "ok"}
   ```

## Manual CloudAccount Validation + EC2 Ingestion Flow

1. Ensure AWS credentials are available in your local environment (for example via AWS CLI profile or environment variables).

2. Start the API server:

   ```bash
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

3. In another terminal, run the manual flow script:

   ```bash
   BASE_URL=http://127.0.0.1:8000 python scripts/manual_test_flow.py
   ```

The script will:
- create a tenant (and retry with a unique name if it receives a 409 conflict)
- create a cloud account under that tenant
- call cloud account validation
- call EC2 ingestion
- print JSON responses for each step
