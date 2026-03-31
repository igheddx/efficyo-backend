from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.user_context import UserContext, get_user_context
from app.cost.service import cost_snapshot_service
from app.services import access_resolution_service, tenant_scope_service

router = APIRouter(prefix="/sync/jobs", tags=["sync"])


class CostSyncStartRequest(BaseModel):
    tenant_id: UUID
    cloud_account_id: UUID
    force_refresh: bool = False


@router.post("/cost", status_code=status.HTTP_200_OK)
def start_cost_sync(
    body: CostSyncStartRequest,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> dict[str, Any]:
    tenant_scope_service.require_tenant_accessible(db_session, ctx, body.tenant_id)
    access_resolution_service.require_min_effective_access(
        db_session,
        ctx,
        tenant_id=body.tenant_id,
        cloud_account_id=body.cloud_account_id,
        minimum="admin",
    )
    try:
        snap = cost_snapshot_service.sync_cost_snapshot(
            db_session,
            tenant_id=body.tenant_id,
            cloud_account_id=body.cloud_account_id,
            feature_name="manual_cost_sync",
            request_type="manual_sync",
            force_refresh=body.force_refresh,
            actor_role=ctx.role,
            actor_is_platform_root=bool(ctx.is_platform_root),
            actor_is_system=False,
        )
        db_session.commit()
    except Exception as exc:
        db_session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {
        "status": "ok",
        "snapshot_id": str(snap.id),
        "snapshot_date": snap.snapshot_date.isoformat(),
        "updated_at": snap.updated_at.isoformat() if snap.updated_at else None,
    }

