from __future__ import annotations

import logging
from datetime import timedelta, timezone
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, utc_now
from app.models.ingestion_job import IngestionJob

# Jobs stuck longer than this are considered orphaned (server crashed mid-run).
_ORPHAN_THRESHOLD_MINUTES: int = 30
from app.services import cloud_account_service, detection_service, ingestion_service, recommendation_service, tenant_service
from app.sync.jobs.cost_sync import CostSyncJobRunner
from app.sync.jobs.resource_sync import run_resource_sync

logger = logging.getLogger(__name__)


class ActiveSyncJobExists(Exception):
    """Another sync job is already queued or running for this cloud account."""

    def __init__(self, job: IngestionJob) -> None:
        self.job = job


def reap_orphaned_jobs(db_session: Session) -> int:
    """Mark any jobs stuck in queued/running longer than the orphan threshold as failed.

    Called once at server startup so a crash/restart never blocks future syncs.
    Returns the number of jobs reaped.
    """
    cutoff = utc_now() - timedelta(minutes=_ORPHAN_THRESHOLD_MINUTES)
    orphans = (
        db_session.query(IngestionJob)
        .filter(
            IngestionJob.status.in_(("queued", "running")),
            IngestionJob.updated_at < cutoff,
        )
        .all()
    )
    for job in orphans:
        job.status = "failed"
        job.error_message = (
            f"Job reaped at startup: was stuck in '{job.status}' state for >"
            f"{_ORPHAN_THRESHOLD_MINUTES} min (likely a server crash)."
        )
        job.updated_at = utc_now()
        logger.warning(
            "Reaped orphaned sync job",
            extra={"job_id": str(job.id), "job_type": job.job_type, "prior_status": job.status},
        )
    if orphans:
        db_session.commit()
    return len(orphans)


def _validate_scope(db_session: Session, tenant_id: UUID, cloud_account_id: UUID) -> None:
    cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)


def get_active_sync_job(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> IngestionJob | None:
    """Return the most recent queued or running job for this scope, if any.

    Any job that has been in an active state longer than the orphan threshold is
    automatically failed here so it cannot permanently block new syncs.
    """
    _validate_scope(db_session, tenant_id, cloud_account_id)
    cutoff = utc_now() - timedelta(minutes=_ORPHAN_THRESHOLD_MINUTES)
    candidate = (
        db_session.query(IngestionJob)
        .filter(
            IngestionJob.tenant_id == tenant_id,
            IngestionJob.cloud_account_id == cloud_account_id,
            IngestionJob.status.in_(("queued", "running")),
        )
        .order_by(IngestionJob.created_at.desc())
        .first()
    )
    if candidate is not None and (
        candidate.updated_at.replace(tzinfo=timezone.utc)
        if candidate.updated_at.tzinfo is None
        else candidate.updated_at
    ) < cutoff:
        logger.warning(
            "Auto-reaping orphaned sync job on access",
            extra={"job_id": str(candidate.id), "job_type": candidate.job_type},
        )
        candidate.status = "failed"
        candidate.error_message = (
            f"Job auto-reaped: stuck in active state for >{_ORPHAN_THRESHOLD_MINUTES} min."
        )
        candidate.updated_at = utc_now()
        db_session.commit()
        return None
    return candidate


def create_sync_job(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    job_type: str = "full_sync",
) -> IngestionJob:
    active = get_active_sync_job(db_session, tenant_id, cloud_account_id)
    if active is not None:
        raise ActiveSyncJobExists(active)
    job = IngestionJob(
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        job_type=job_type,
        status="queued",
    )
    db_session.add(job)
    try:
        db_session.commit()
        db_session.refresh(job)
    except IntegrityError:
        db_session.rollback()
        existing = get_active_sync_job(db_session, tenant_id, cloud_account_id)
        if existing is not None:
            raise ActiveSyncJobExists(existing) from None
        raise
    return job


def list_sync_jobs(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    limit: int = 20,
) -> list[IngestionJob]:
    _validate_scope(db_session, tenant_id, cloud_account_id)
    return (
        db_session.query(IngestionJob)
        .filter(
            IngestionJob.tenant_id == tenant_id,
            IngestionJob.cloud_account_id == cloud_account_id,
        )
        .order_by(IngestionJob.created_at.desc())
        .limit(limit)
        .all()
    )


def get_sync_job(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    job_id: UUID,
) -> IngestionJob:
    _validate_scope(db_session, tenant_id, cloud_account_id)
    job = (
        db_session.query(IngestionJob)
        .filter(
            IngestionJob.id == job_id,
            IngestionJob.tenant_id == tenant_id,
            IngestionJob.cloud_account_id == cloud_account_id,
        )
        .first()
    )
    if job is None:
        raise ValueError("sync_job_not_found")
    return job


def _run_sync_pipeline(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    job_type: str,
    sync_run_id: UUID,
) -> None:
    if job_type in {"full_sync", "analysis_refresh", "resource_sync"}:
        run_resource_sync(
            db_session,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            sync_run_id=sync_run_id,
        )
        return

    if job_type in {"cost_refresh", "cost_sync"}:
        runner = CostSyncJobRunner()
        runner.run(
            db_session,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            sync_job_id=sync_run_id,
            force_refresh=(job_type == "cost_refresh"),
            actor_role="system",
            actor_is_platform_root=False,
            actor_is_system=True,
        )
        # Re-run analyzers/recommendations against snapshots so findings stay aligned.
        detection_service.detect_ec2_findings(db_session, tenant_id, cloud_account_id, sync_run_id)
        recommendation_service.generate_rds_recommendations(db_session, tenant_id, cloud_account_id, sync_run_id=sync_run_id)
        return


def _failure_message(exc: BaseException) -> str:
    text = (str(exc) or "").strip() or type(exc).__name__
    return (text or "Sync job failed")[:2000]


def _emit_sync_notifications(db_session: Session, job_id: UUID) -> None:
    """Best-effort in-app alerts after a job reaches completed or failed."""
    try:
        from app.services import approvals_service, notification_service

        job = db_session.query(IngestionJob).filter(IngestionJob.id == job_id).first()
        if job is None or job.status not in ("completed", "failed"):
            return
        ok = job.status == "completed"
        notification_service.notify_sync_terminal(
            db_session,
            tenant_id=job.tenant_id,
            cloud_account_id=job.cloud_account_id,
            job_id=job.id,
            success=ok,
            error_message=job.error_message if not ok else None,
        )
        # Do NOT send approval_required notifications after sync. Unactioned recommendations
        # are not pending approvals — an approval request must be explicitly submitted first.
    except Exception:
        logger.exception("Failed to emit sync notifications", extra={"job_id": str(job_id)})
        try:
            db_session.rollback()
        except Exception:
            pass


def _mark_job_terminal_failed(db_session: Session, job_id: UUID, exc: BaseException) -> None:
    """Persist failed terminal state; safe to call after a prior commit/rollback."""
    job = db_session.query(IngestionJob).filter(IngestionJob.id == job_id).first()
    if job is None:
        return
    if job.status not in ("queued", "running"):
        return
    now = utc_now()
    job.status = "failed"
    job.completed_at = now
    job.updated_at = now
    job.error_message = _failure_message(exc)
    db_session.add(job)
    db_session.commit()


def execute_sync_job(job_id: UUID) -> None:
    db_session = SessionLocal()
    try:
        job = db_session.query(IngestionJob).filter(IngestionJob.id == job_id).first()
        if job is None:
            return
        # Only jobs created as queued should run; avoids double execution.
        if job.status != "queued":
            return

        job.status = "running"
        job.started_at = utc_now()
        job.error_message = None
        job.updated_at = utc_now()
        db_session.add(job)
        db_session.commit()
        db_session.refresh(job)

        try:
            tenant_service.refresh_tipwave_demo_cloud_account_metadata(db_session)
            _run_sync_pipeline(db_session, job.tenant_id, job.cloud_account_id, job.job_type, job.id)
            job.status = "completed"
            job.completed_at = utc_now()
            job.error_message = None
            job.updated_at = utc_now()
            db_session.add(job)
            db_session.commit()
            _emit_sync_notifications(db_session, job.id)
        except Exception as exc:
            logger.exception("Sync job failed", extra={"job_id": str(job_id)})
            err_text = _failure_message(exc)
            job.status = "failed"
            job.completed_at = utc_now()
            job.error_message = err_text
            job.updated_at = utc_now()
            # started_at remains set when failure occurs after run began
            db_session.add(job)
            try:
                db_session.commit()
                _emit_sync_notifications(db_session, job.id)
            except Exception as commit_exc:
                logger.exception(
                    "Failed to commit sync job failure row",
                    extra={"job_id": str(job_id)},
                )
                db_session.rollback()
                try:
                    _mark_job_terminal_failed(db_session, job_id, commit_exc)
                    _emit_sync_notifications(db_session, job_id)
                except Exception:
                    logger.exception(
                        "Failed to recover sync job failure state",
                        extra={"job_id": str(job_id)},
                    )
                    db_session.rollback()
    except Exception as exc:
        logger.exception("Sync job worker crashed", extra={"job_id": str(job_id)})
        try:
            db_session.rollback()
        except Exception:
            pass
        try:
            _mark_job_terminal_failed(db_session, job_id, exc)
            _emit_sync_notifications(db_session, job_id)
        except Exception:
            logger.exception(
                "Failed to persist sync job failure after worker crash",
                extra={"job_id": str(job_id)},
            )
            db_session.rollback()
    finally:
        db_session.close()

