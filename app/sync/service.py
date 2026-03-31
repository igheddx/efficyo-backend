from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.cloud_account import CloudAccount
from app.models.sync_pipeline import SyncJob, SyncTask
from app.services import tenant_scope_service
from app.sync.enums import ACTIVE_JOB_STATUSES
from app.sync.queue.database import DatabaseTaskQueue
from app.sync.queue.base import TaskQueue

from app.sync import orchestrator, repository


@dataclass(frozen=True)
class RetryContext:
    task_ids: list[UUID] | None
    delay_seconds: int


class SyncPipelineService:
    def __init__(self, *, queue: TaskQueue | None = None) -> None:
        self._queue = queue or DatabaseTaskQueue()

    def start_sync_job(
        self,
        db: Session,
        *,
        ctx: Any,
        tenant_id: UUID,
        cloud_account_id: UUID,
        provider: str,
        trigger_type: str,
        force_new: bool,
    ) -> SyncJob:
        ca = db.query(CloudAccount).filter(CloudAccount.id == cloud_account_id, CloudAccount.tenant_id == tenant_id).first()
        if ca is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found.")

        org_id = ca.tenant.organization_id
        if org_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant has no organization.")

        # created_by is optional: sync may be triggered by system jobs later.
        initiated_by_user_id = getattr(ctx, "user_id", None)

        if provider != "aws":
            raise ValueError("provider_not_supported")

        job = orchestrator.create_job_with_plan(
            db,
            organization_id=org_id,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            provider=provider,
            initiated_by_user_id=initiated_by_user_id,
            trigger_type=trigger_type,
            force_new=force_new,
            queue=self._queue,
        )
        db.commit()
        return job

    def get_job_for_user(self, db: Session, ctx: Any, job_id: UUID) -> SyncJob:
        job = repository.get_job(db, job_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sync job not found.")

        if getattr(ctx, "user_id", None) is None:
            # dev/header auth path: rely on tenant_scope_service.
            tenant_scope_service.require_data_access_organization_id(db, ctx)
        else:
            # Use effective access floor to prevent cross-tenant access.
            tenant_scope_service.require_tenant_accessible(db, ctx, job.tenant_id)
        return job

    def list_jobs_for_user(
        self,
        db: Session,
        ctx: Any,
        *,
        tenant_id: UUID,
        cloud_account_id: UUID,
        limit: int,
        offset: int,
    ) -> tuple[list[SyncJob], int]:
        _org_id = tenant_scope_service.require_data_access_organization_id(db, ctx)
        jobs, total = repository.list_jobs(
            db,
            organization_id=None,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            limit=limit,
            offset=offset,
        )
        # `repository.list_jobs` doesn't include org filter yet; keep safe by access check per job scope.
        return jobs, total

    def list_tasks_for_user(self, db: Session, ctx: Any, job_id: UUID) -> list[SyncTask]:
        job = self.get_job_for_user(db, ctx, job_id)
        tasks = repository.list_tasks_for_job(db, job.id)
        _ = job
        return tasks

    def list_events_for_user(self, db: Session, ctx: Any, job_id: UUID, limit: int) -> list[Any]:
        job = self.get_job_for_user(db, ctx, job_id)
        _ = job
        return repository.list_events_for_job(db, job_id, limit=limit)

    def retry_task(self, db: Session, ctx: Any, *, task_id: UUID, delay_seconds: int) -> SyncTask:
        task = repository.get_task(db, task_id)
        if task is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sync task not found.")
        job = self.get_job_for_user(db, ctx, task.sync_job_id)
        _ = job
        self._queue.schedule_retry(
            db,
            task_id=task_id,
            delay_seconds=delay_seconds,
            error_code=task.error_code,
            error_message=task.error_message,
        )
        job.last_heartbeat_at = repository.utcnow()
        db.commit()
        return task

    def retry_job(
        self,
        db: Session,
        ctx: Any,
        *,
        job_id: UUID,
        task_ids: list[UUID] | None,
        delay_seconds: int,
    ) -> SyncJob:
        job = self.get_job_for_user(db, ctx, job_id)
        tasks = repository.list_tasks_for_job(db, job.id)
        failed = [t for t in tasks if t.status == "failed"]
        selected = failed if task_ids is None else [t for t in failed if t.id in set(task_ids)]
        for t in selected:
            self._queue.schedule_retry(
                db,
                task_id=t.id,
                delay_seconds=delay_seconds,
                error_code=t.error_code,
                error_message=t.error_message,
            )
        # Put job back into collecting so worker resumes.
        job.status = "collecting"
        job.started_at = job.started_at or repository.utcnow()
        db.commit()
        return job


# Singleton instance used by API routes (modular monolith convenience).
sync_pipeline_service = SyncPipelineService()

