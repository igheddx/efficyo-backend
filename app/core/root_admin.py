"""Platform root administrator guard (User.is_root_admin)."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.user_context import UserContext, get_user_context
from app.models.user import User


def require_root_admin(
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> UserContext:
    if ctx.user_id is not None:
        user = db.query(User).filter(User.id == ctx.user_id).first()
        if user is not None and user.is_root_admin and (user.status or "active") == "active":
            return ctx
    elif settings.allow_dev_header_auth and ctx.user_id is None and ctx.is_platform_root:
        return ctx
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Root administrator access required.",
    )
