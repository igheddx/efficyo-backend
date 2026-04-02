from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.api.v1.router import router as api_v1_router
from app.core.db import SessionLocal
from app.core.logging import configure_logging
from app.services import auth_service

configure_logging()

WORKER_HEARTBEAT_PATH = Path("/app/runtime/worker-heartbeat.json")
WORKER_HEARTBEAT_MAX_AGE_SECONDS = 180


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db = SessionLocal()
    try:
        try:
            auth_service.ensure_local_seed_users(db)
        except (OperationalError, ProgrammingError):
            # Tests override `get_db` with SQLite; dev DB may be behind migrations.
            pass
    finally:
        db.close()
    yield


app = FastAPI(
    title="AWS Autonomous Cloud Optimizer API",
    version="0.1.0",
    lifespan=lifespan,
)

# Session cookies + optional cross-origin calls from Vite (direct :8000) require credentials.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
        "http://fptnext.local:5173",
        "http://fptnext.local:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(OperationalError)
async def database_unavailable_handler(_request: Request, _exc: OperationalError) -> JSONResponse:
    """Avoid opaque 500s when Postgres is down or DATABASE_URL is wrong."""
    return JSONResponse(
        status_code=503,
        content={
            "detail": "Database unavailable. Check DATABASE_URL, Postgres on the expected host/port, and that migrations ran.",
        },
    )


@app.exception_handler(ProgrammingError)
async def database_schema_handler(_request: Request, _exc: ProgrammingError) -> JSONResponse:
    """Missing tables / wrong schema after DB reset or before alembic upgrade."""
    return JSONResponse(
        status_code=503,
        content={
            "detail": (
                "Database schema is missing or out of date. With Postgres running and DATABASE_URL set in backend/.env, "
                "run from the backend directory: alembic upgrade head"
            ),
        },
    )


@app.get("/health", tags=["health"])
async def health_check() -> JSONResponse:
    db_status = "ok"
    worker_status = "stale"
    worker_last_seen: str | None = None
    http_status = status.HTTP_200_OK

    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"
        http_status = status.HTTP_503_SERVICE_UNAVAILABLE
    finally:
        db.close()

    if WORKER_HEARTBEAT_PATH.is_file():
        heartbeat_age = datetime.now(timezone.utc).timestamp() - WORKER_HEARTBEAT_PATH.stat().st_mtime
        worker_last_seen = datetime.fromtimestamp(
            WORKER_HEARTBEAT_PATH.stat().st_mtime,
            tz=timezone.utc,
        ).isoformat()
        if heartbeat_age <= WORKER_HEARTBEAT_MAX_AGE_SECONDS:
            worker_status = "ok"
        else:
            http_status = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        http_status = status.HTTP_503_SERVICE_UNAVAILABLE

    overall_status = "ok" if http_status == status.HTTP_200_OK else "degraded"
    return JSONResponse(
        status_code=http_status,
        content={
            "status": overall_status,
            "database": db_status,
            "worker": worker_status,
            "worker_last_seen": worker_last_seen,
        },
    )


app.include_router(api_v1_router, prefix="/api")
