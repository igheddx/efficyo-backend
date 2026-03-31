from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.user_context import UserContext, get_user_context
from app.schemas.sync_pipeline import (
    RetrySyncJobRequest,
    RetrySyncTaskRequest,
    SyncJobCreate,
    SyncJobEventRead,
    SyncJobRead,
    SyncTaskRead,
)
from app.sync import repository
from app.services import access_resolution_service, tenant_scope_service
from app.sync.service import sync_pipeline_service


router = APIRouter(prefix="/sync", tags=["sync"])


def _require_cloud_admin(db_session: Session, ctx: UserContext, tenant_id: UUID, cloud_account_id: UUID) -> None:
    tenant_scope_service.require_tenant_accessible(db_session, ctx, tenant_id)
    access_resolution_service.require_min_effective_access(
        db_session,
        ctx,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        minimum="admin",
    )


@router.post("/jobs", response_model=SyncJobRead, status_code=status.HTTP_201_CREATED)
def start_sync_job(
    body: SyncJobCreate,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> SyncJobRead:
    _require_cloud_admin(db_session, ctx, tenant_id=body.tenant_id, cloud_account_id=body.cloud_account_id)
    job = sync_pipeline_service.start_sync_job(
        db_session,
        ctx=ctx,
        tenant_id=body.tenant_id,
        cloud_account_id=body.cloud_account_id,
        provider=body.provider,
        trigger_type=body.trigger_type,
        force_new=body.force_new,
    )
    return SyncJobRead.model_validate(job)


@router.get("/jobs/{job_id}", response_model=SyncJobRead, status_code=status.HTTP_200_OK)
def get_sync_job(
    job_id: UUID,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> SyncJobRead:
    job = sync_pipeline_service.get_job_for_user(db_session, ctx, job_id)
    return SyncJobRead.model_validate(job)


@router.get("/jobs", status_code=status.HTTP_200_OK)
def list_sync_jobs(
    tenant_id: UUID = Query(...),
    cloud_account_id: UUID = Query(...),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> dict[str, Any]:
    _ = tenant_scope_service.require_data_access_organization_id(db_session, ctx)
    jobs, total = sync_pipeline_service.list_jobs_for_user(
        db_session,
        ctx,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        limit=limit,
        offset=offset,
    )
    return {"items": [SyncJobRead.model_validate(j) for j in jobs], "total": total}


@router.get("/jobs/{job_id}/tasks", response_model=list[SyncTaskRead], status_code=status.HTTP_200_OK)
def list_sync_job_tasks(
    job_id: UUID,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> list[SyncTaskRead]:
    tasks = sync_pipeline_service.list_tasks_for_user(db_session, ctx, job_id)
    return [SyncTaskRead.model_validate(t) for t in tasks]


@router.get("/jobs/{job_id}/events", response_model=list[SyncJobEventRead], status_code=status.HTTP_200_OK)
def list_sync_job_events(
    job_id: UUID,
    limit: int = Query(200, ge=1, le=500),
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> list[SyncJobEventRead]:
    events = sync_pipeline_service.list_events_for_user(db_session, ctx, job_id, limit=limit)
    return [SyncJobEventRead.model_validate(e) for e in events]


@router.post("/jobs/{job_id}/retry", response_model=SyncJobRead, status_code=status.HTTP_200_OK)
def retry_sync_job(
    job_id: UUID,
    body: RetrySyncJobRequest,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> SyncJobRead:
    job = sync_pipeline_service.get_job_for_user(db_session, ctx, job_id)
    _require_cloud_admin(db_session, ctx, tenant_id=job.tenant_id, cloud_account_id=job.cloud_account_id)
    task_ids = body.task_ids if body.task_ids else None
    updated = sync_pipeline_service.retry_job(
        db_session,
        ctx,
        job_id=job_id,
        task_ids=task_ids,
        delay_seconds=body.delay_seconds,
    )
    return SyncJobRead.model_validate(updated)


@router.post("/tasks/{task_id}/retry", response_model=SyncTaskRead, status_code=status.HTTP_200_OK)
def retry_sync_task(
    task_id: UUID,
    body: RetrySyncTaskRequest,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> SyncTaskRead:
    task = repository.get_task(db_session, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sync task not found.")
    job = sync_pipeline_service.get_job_for_user(db_session, ctx, task.sync_job_id)
    _require_cloud_admin(db_session, ctx, tenant_id=job.tenant_id, cloud_account_id=job.cloud_account_id)
    updated_task = sync_pipeline_service.retry_task(
        db_session,
        ctx,
        task_id=task_id,
        delay_seconds=body.delay_seconds,
    )
    return SyncTaskRead.model_validate(updated_task)

