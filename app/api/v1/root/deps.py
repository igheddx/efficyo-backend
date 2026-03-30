"""Dependencies for platform root (operator) control-plane routes."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status

from app.core.user_context import UserContext, get_user_context


def require_platform_root(ctx: UserContext = Depends(get_user_context)) -> UserContext:
    """Only `User.is_root_admin` (session) or dev-header `root_admin` when enabled."""
    if not ctx.is_platform_root:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform root administrator access required.",
        )
    return ctx
