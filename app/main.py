from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.api.v1.router import router as api_v1_router
from app.core.db import SessionLocal
from app.core.logging import configure_logging
from app.services import auth_service

configure_logging()


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
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(api_v1_router, prefix="/api")
