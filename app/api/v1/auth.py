from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.schemas.auth import LoginRequest, LoginResponse
from app.services import auth_service

# Exposes POST /api/v1/login and POST /api/v1/logout (no /auth prefix).
router = APIRouter(tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(
    body: LoginRequest,
    response: Response,
    db_session: Session = Depends(get_db),
) -> LoginResponse:
    try:
        auth_service.ensure_local_seed_users(db_session)
    except ProgrammingError:
        db_session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Database schema is out of date (e.g. missing organizations.slug after an upgrade). "
                "With Postgres running and DATABASE_URL set, run from the backend directory: alembic upgrade head"
            ),
        ) from None
    user, raw = auth_service.login_with_password(db_session, body.login, body.password)
    auth_service.set_session_cookie(response, raw)
    return LoginResponse(email=user.email, display_name=user.display_name)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    db_session: Session = Depends(get_db),
) -> Response:
    raw = request.cookies.get(settings.session_cookie_name)
    if raw:
        row = auth_service.get_session_by_raw_token(db_session, raw)
        if row is not None:
            auth_service.delete_session(db_session, row)
    auth_service.clear_session_cookie(response)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
