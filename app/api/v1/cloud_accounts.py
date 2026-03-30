from uuid import UUID, uuid4
import logging
from botocore.exceptions import ClientError

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.cost_window import account_cost_window_fields, wow_ce_14d_window_fields
from app.core.db import get_db
from app.core.user_context import UserContext, get_user_context
from app.schemas.cloud_account import (
    ActionPlanRead,
    CostTrendsSeriesRead,
    CloudAccountCreate,
    CloudAccountProvisionedRead,
    CloudAccountRead,
    CloudAccountTestConnectionRequest,
    CloudAccountTestConnectionResponse,
    CostSummaryRead,
    Ec2OtherBreakdownRead,
    CloudAccountValidationRead,
    DetectionRunRead,
    FindingRead,
    RecommendationRead,
    RecommendationRunRead,
    ResourceIngestionRead,
    TopCostServiceRead,
    CostTrendRead,
    CostTrendsListRead,
    SummaryRecommendationRead,
    SummaryRead,
    SimulationRead,
    SavingsTrendsSeriesRead,
    TopOpportunityRead,
    InsightsRead,
)
from app.schemas.approvals import RecommendationRejectRequest
from app.schemas.ingestion_job import IngestionJobCreate, IngestionJobRead
from app.schemas.recommendation_outcome import (
    RecommendationAppliedUpdate,
    RecommendationApprovalUpdate,
    RecommendationDryRunRead,
    RecommendationExecuteRead,
    RecommendationExecuteRequest,
    RecommendationOutcomeCreate,
    RecommendationOutcomeRead,
    RecommendationOutcomeUpdate,
    RecommendationPreflightRead,
    RecommendationWorkflowProgressRead,
    RecommendationWorkflowRead,
    RecommendationWorkflowTimelineRead,
    SavingsProofSummaryRead,
)
from app.services.ingestion_job_service import ActiveSyncJobExists
from app.services import (
    access_resolution_service,
    approval_request_service,
    cloud_account_reset_service,
    cloud_account_service,
    cost_summary_service,
    detection_service,
    ingestion_job_service,
    ingestion_service,
    outcome_impact_service,
    preflight_dry_run_service,
    recommendation_service,
    recommendation_outcome_service,
    safe_execution_service,
    simulation_service,
    summary_service,
    tenant_scope_service,
    trend_service,
    insight_narration_service,
)


def require_org_scoped_cloud_tenant(
    tenant_id: UUID,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> None:
    tenant_scope_service.require_tenant_accessible(db_session, ctx, tenant_id)


def _min_access_for_cloud_route(request: Request) -> str | None:
    """
    Map matched route template + method to minimum effective access for {cloud_account_id} routes.
    Returns None when this route has no cloud_account_id path param (caller skips).
    """
    route = request.scope.get("route")
    path = (getattr(route, "path", "") or "") if route else ""
    if "{cloud_account_id}" not in path:
        return None
    method = request.method.upper()
    if path.endswith("/approve") or path.endswith("/reject"):
        return "approver"
    if path.endswith("/execute") or path.endswith("/mark-applied") or path.endswith("/mark-acted-on"):
        return "admin"
    if path.endswith("/preflight") or path.endswith("/dry-run") or path.endswith("/simulate"):
        return "viewer"
    if "ingestion-data/reset" in path:
        return "admin"
    if path.endswith("/validate"):
        return "admin"
    if method == "POST" and path.endswith("/sync"):
        return "admin"
    if method == "POST" and ("/ingest/" in path or "/detect/" in path or "/recommend/" in path):
        return "admin"
    if method == "POST" and "/outcomes" in path:
        return "admin"
    return "viewer"


def require_cloud_route_effective_access(
    request: Request,
    tenant_id: UUID,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> None:
    minimum = _min_access_for_cloud_route(request)
    if minimum is None:
        return
    cid_raw = request.path_params.get("cloud_account_id")
    if not cid_raw:
        return
    try:
        cloud_account_id = UUID(str(cid_raw))
    except ValueError:
        return
    access_resolution_service.require_min_effective_access(
        db_session, ctx, tenant_id, cloud_account_id, minimum=minimum  # type: ignore[arg-type]
    )


router = APIRouter(
    prefix="/tenants/{tenant_id}/cloud-accounts",
    tags=["cloud-accounts"],
    dependencies=[
        Depends(require_org_scoped_cloud_tenant),
        Depends(require_cloud_route_effective_access),
    ],
)
logger = logging.getLogger(__name__)

_AWS_AUTH_ERROR_CODES = frozenset({
    "UnauthorizedOperation",
    "AccessDenied",
    "AccessDeniedException",
    "AuthFailure",
    "InvalidClientTokenId",
    "ExpiredTokenException",
})

_AWS_CE_UNAVAILABLE_ERROR_CODES = frozenset({
    "DataUnavailableException",
    "BillingViewHealthStatusException",
    "ServiceUnavailableException",
})


@router.post("", response_model=CloudAccountProvisionedRead, status_code=status.HTTP_201_CREATED)
def create_cloud_account_endpoint(
    tenant_id: UUID,
    body: CloudAccountCreate,
    background_tasks: BackgroundTasks,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> CloudAccountProvisionedRead:
    tenant_scope_service.require_tenant_write_role(ctx)
    try:
        cloud_account = cloud_account_service.create_cloud_account(db_session, tenant_id, body)
    except ValueError as exc:
        if str(exc) == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        if str(exc) == "cloud_account_duplicate":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"A cloud account for AWS account {body.account_id} is already linked to this customer. "
                    "Use the existing account from the dashboard or remove the duplicate before onboarding again."
                ),
            ) from exc
        raise
    except IntegrityError as exc:
        db_session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A cloud account for this AWS account ID is already linked to this customer "
                "(unique constraint). Choose another account or use the existing link."
            ),
        ) from exc

    initial_sync_job = None
    if body.trigger_initial_sync:
        try:
            job = ingestion_job_service.create_sync_job(
                db_session=db_session,
                tenant_id=tenant_id,
                cloud_account_id=cloud_account.id,
                job_type="full_sync",
            )
            background_tasks.add_task(ingestion_job_service.execute_sync_job, job.id)
            initial_sync_job = IngestionJobRead.model_validate(job)
        except ActiveSyncJobExists as exc:
            initial_sync_job = IngestionJobRead.model_validate(exc.job)

    base = CloudAccountRead.model_validate(cloud_account)
    return CloudAccountProvisionedRead(**base.model_dump(), initial_sync_job=initial_sync_job)


@router.get("", response_model=list[CloudAccountRead])
def list_cloud_accounts_endpoint(
    tenant_id: UUID,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> list[CloudAccountRead]:
    try:
        cloud_accounts = cloud_account_service.list_cloud_accounts(db_session, tenant_id)
    except ValueError as exc:
        if str(exc) == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        raise

    allowed = access_resolution_service.filter_cloud_accounts_visible(
        db_session,
        ctx,
        tenant_id,
        [c.id for c in cloud_accounts],
    )
    allowed_set = set(allowed)
    visible = [c for c in cloud_accounts if c.id in allowed_set]
    return [CloudAccountRead.model_validate(item) for item in visible]


@router.post(
    "/test-connection",
    response_model=CloudAccountTestConnectionResponse,
    status_code=status.HTTP_200_OK,
)
def test_cloud_account_connection_endpoint(
    tenant_id: UUID,
    body: CloudAccountTestConnectionRequest,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> CloudAccountTestConnectionResponse:
    """STS assume-role against credentials not yet saved (onboarding)."""
    tenant_scope_service.require_tenant_write_role(ctx)
    result = cloud_account_service.test_prospective_cloud_connection(
        body.account_id,
        body.role_arn,
        body.region_default,
    )
    return CloudAccountTestConnectionResponse(
        success=result.success,
        error_message=result.error_message,
        aws_account_id=result.aws_account_id,
        arn=result.arn,
        user_id=result.user_id,
    )


@router.get("/{cloud_account_id}", response_model=CloudAccountRead)
def get_cloud_account_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    db_session: Session = Depends(get_db),
) -> CloudAccountRead:
    try:
        cloud_account = cloud_account_service.get_cloud_account(db_session, tenant_id, cloud_account_id)
    except ValueError as exc:
        if str(exc) == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        raise

    if cloud_account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found")

    return CloudAccountRead.model_validate(cloud_account)


@router.post("/{cloud_account_id}/validate", response_model=CloudAccountValidationRead, status_code=status.HTTP_200_OK)
def validate_cloud_account_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    db_session: Session = Depends(get_db),
) -> CloudAccountValidationRead:
    try:
        cloud_account, validation_result = cloud_account_service.validate_cloud_account(
            db_session, tenant_id, cloud_account_id
        )
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        elif error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise

    return CloudAccountValidationRead(
        cloud_account_id=cloud_account.id,
        status=cloud_account.status,
        success=validation_result.success,
        aws_account_id=validation_result.aws_account_id,
        arn=validation_result.arn,
        user_id=validation_result.user_id,
        error_message=validation_result.error_message,
    )


@router.post(
    "/{cloud_account_id}/ingestion-data/reset",
    status_code=status.HTTP_200_OK,
)
def reset_cloud_account_ingestion_data_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    user_ctx: UserContext = Depends(get_user_context),
    db_session: Session = Depends(get_db),
) -> dict[str, dict[str, int]]:
    """
    Delete all ingestion-derived rows for this cloud account (snapshots, findings,
    recommendations, outcomes, sync job history). Use before a full re-sync from AWS.
    Requires a platform root session (`User.is_root_admin`) or dev header with root_admin role.
    """
    if not user_ctx.is_platform_root:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Clearing ingestion data requires platform root access.",
        )
    try:
        deleted = cloud_account_reset_service.clear_ingested_data_for_cloud_account(
            db_session,
            tenant_id,
            cloud_account_id,
        )
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        if error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise
    return {"deleted": deleted}


@router.post(
    "/{cloud_account_id}/sync",
    response_model=IngestionJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_sync_job_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    background_tasks: BackgroundTasks,
    body: IngestionJobCreate | None = None,
    db_session: Session = Depends(get_db),
) -> IngestionJobRead:
    try:
        job = ingestion_job_service.create_sync_job(
            db_session=db_session,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            job_type=(body.job_type if body is not None else "full_sync"),
        )
    except ActiveSyncJobExists as exc:
        active = IngestionJobRead.model_validate(exc.job)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "A sync job is already queued or running for this cloud account.",
                "active_job": active.model_dump(mode="json"),
            },
        ) from exc
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        if error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise

    background_tasks.add_task(ingestion_job_service.execute_sync_job, job.id)
    return IngestionJobRead.model_validate(job)


@router.get(
    "/{cloud_account_id}/sync-jobs",
    response_model=list[IngestionJobRead],
    status_code=status.HTTP_200_OK,
)
def list_sync_jobs_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    db_session: Session = Depends(get_db),
) -> list[IngestionJobRead]:
    try:
        rows = ingestion_job_service.list_sync_jobs(
            db_session=db_session,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            limit=limit,
        )
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        if error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise
    return [IngestionJobRead.model_validate(r) for r in rows]


@router.get(
    "/{cloud_account_id}/sync-jobs/{job_id}",
    response_model=IngestionJobRead,
    status_code=status.HTTP_200_OK,
)
def get_sync_job_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    job_id: UUID,
    db_session: Session = Depends(get_db),
) -> IngestionJobRead:
    try:
        row = ingestion_job_service.get_sync_job(
            db_session=db_session,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            job_id=job_id,
        )
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        if error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        if error_msg == "sync_job_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sync job not found") from exc
        raise
    return IngestionJobRead.model_validate(row)


@router.post("/{cloud_account_id}/ingest/ec2", response_model=ResourceIngestionRead, status_code=status.HTTP_200_OK)
def ingest_ec2_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    db_session: Session = Depends(get_db),
) -> ResourceIngestionRead:
    try:
        result = ingestion_service.ingest_ec2(db_session, tenant_id, cloud_account_id)
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        elif error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        error_type = "aws_authorization_error" if error_code in _AWS_AUTH_ERROR_CODES else "aws_client_error"
        logger.exception(
            "AWS ClientError during EC2 ingestion",
            extra={"tenant_id": str(tenant_id), "cloud_account_id": str(cloud_account_id), "aws_error_code": error_code},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error_type": error_type, "aws_error_code": error_code, "message": exc.response["Error"]["Message"]},
        ) from exc
    except Exception:
        logger.exception(
            "EC2 ingestion failed at API layer",
            extra={"tenant_id": str(tenant_id), "cloud_account_id": str(cloud_account_id)},
        )
        raise

    return ResourceIngestionRead(
        cloud_account_id=result.cloud_account_id,
        resource_type=result.resource_type,
        ingested_count=result.ingested_count,
        captured_at=result.captured_at,
    )


@router.post("/{cloud_account_id}/ingest/rds", response_model=ResourceIngestionRead, status_code=status.HTTP_200_OK)
def ingest_rds_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    db_session: Session = Depends(get_db),
) -> ResourceIngestionRead:
    try:
        result = ingestion_service.ingest_rds(db_session, tenant_id, cloud_account_id)
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        elif error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        error_type = "aws_authorization_error" if error_code in _AWS_AUTH_ERROR_CODES else "aws_client_error"
        logger.exception(
            "AWS ClientError during RDS ingestion",
            extra={"tenant_id": str(tenant_id), "cloud_account_id": str(cloud_account_id), "aws_error_code": error_code},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error_type": error_type, "aws_error_code": error_code, "message": exc.response["Error"]["Message"]},
        ) from exc
    except Exception:
        logger.exception(
            "RDS ingestion failed at API layer",
            extra={"tenant_id": str(tenant_id), "cloud_account_id": str(cloud_account_id)},
        )
        raise

    return ResourceIngestionRead(
        cloud_account_id=result.cloud_account_id,
        resource_type=result.resource_type,
        ingested_count=result.ingested_count,
        captured_at=result.captured_at,
    )


@router.post("/{cloud_account_id}/ingest/lambda", response_model=ResourceIngestionRead, status_code=status.HTTP_200_OK)
def ingest_lambda_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    db_session: Session = Depends(get_db),
) -> ResourceIngestionRead:
    try:
        result = ingestion_service.ingest_lambda(db_session, tenant_id, cloud_account_id)
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        elif error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        error_type = "aws_authorization_error" if error_code in _AWS_AUTH_ERROR_CODES else "aws_client_error"
        logger.exception(
            "AWS ClientError during Lambda ingestion",
            extra={"tenant_id": str(tenant_id), "cloud_account_id": str(cloud_account_id), "aws_error_code": error_code},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error_type": error_type, "aws_error_code": error_code, "message": exc.response["Error"]["Message"]},
        ) from exc
    except Exception:
        logger.exception(
            "Lambda ingestion failed at API layer",
            extra={"tenant_id": str(tenant_id), "cloud_account_id": str(cloud_account_id)},
        )
        raise

    return ResourceIngestionRead(
        cloud_account_id=result.cloud_account_id,
        resource_type=result.resource_type,
        ingested_count=result.ingested_count,
        captured_at=result.captured_at,
    )


@router.post("/{cloud_account_id}/ingest/ebs", response_model=ResourceIngestionRead, status_code=status.HTTP_200_OK)
def ingest_ebs_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    db_session: Session = Depends(get_db),
) -> ResourceIngestionRead:
    try:
        result = ingestion_service.ingest_ebs(db_session, tenant_id, cloud_account_id)
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        elif error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        error_type = "aws_authorization_error" if error_code in _AWS_AUTH_ERROR_CODES else "aws_client_error"
        logger.exception(
            "AWS ClientError during EBS ingestion",
            extra={"tenant_id": str(tenant_id), "cloud_account_id": str(cloud_account_id), "aws_error_code": error_code},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error_type": error_type, "aws_error_code": error_code, "message": exc.response["Error"]["Message"]},
        ) from exc
    except Exception:
        logger.exception(
            "EBS ingestion failed at API layer",
            extra={"tenant_id": str(tenant_id), "cloud_account_id": str(cloud_account_id)},
        )
        raise

    return ResourceIngestionRead(
        cloud_account_id=result.cloud_account_id,
        resource_type=result.resource_type,
        ingested_count=result.ingested_count,
        captured_at=result.captured_at,
    )


@router.post("/{cloud_account_id}/detect/ec2", response_model=DetectionRunRead, status_code=status.HTTP_200_OK)
def detect_ec2_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    db_session: Session = Depends(get_db),
) -> DetectionRunRead:
    sync_run_id = uuid4()
    try:
        result = detection_service.detect_ec2_findings(
            db_session, tenant_id, cloud_account_id, sync_run_id
        )
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        elif error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise

    return DetectionRunRead(
        cloud_account_id=result.cloud_account_id,
        resource_type=result.resource_type,
        findings_created=result.findings_created,
        detected_at=result.detected_at,
        sync_run_id=result.sync_run_id,
    )


@router.post("/{cloud_account_id}/detect/ebs", response_model=DetectionRunRead, status_code=status.HTTP_200_OK)
def detect_ebs_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    db_session: Session = Depends(get_db),
) -> DetectionRunRead:
    sync_run_id = uuid4()
    try:
        result = detection_service.detect_ebs_findings(
            db_session, tenant_id, cloud_account_id, sync_run_id
        )
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        elif error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise

    return DetectionRunRead(
        cloud_account_id=result.cloud_account_id,
        resource_type=result.resource_type,
        findings_created=result.findings_created,
        detected_at=result.detected_at,
        sync_run_id=result.sync_run_id,
    )


@router.post("/{cloud_account_id}/detect/rds", response_model=DetectionRunRead, status_code=status.HTTP_200_OK)
def detect_rds_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    db_session: Session = Depends(get_db),
) -> DetectionRunRead:
    sync_run_id = uuid4()
    try:
        result = detection_service.detect_rds_findings(
            db_session, tenant_id, cloud_account_id, sync_run_id
        )
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        elif error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise

    return DetectionRunRead(
        cloud_account_id=result.cloud_account_id,
        resource_type=result.resource_type,
        findings_created=result.findings_created,
        detected_at=result.detected_at,
        sync_run_id=result.sync_run_id,
    )


@router.post("/{cloud_account_id}/detect/lambda", response_model=DetectionRunRead, status_code=status.HTTP_200_OK)
def detect_lambda_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    db_session: Session = Depends(get_db),
) -> DetectionRunRead:
    sync_run_id = uuid4()
    try:
        result = detection_service.detect_lambda_findings(
            db_session, tenant_id, cloud_account_id, sync_run_id
        )
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        elif error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise

    return DetectionRunRead(
        cloud_account_id=result.cloud_account_id,
        resource_type=result.resource_type,
        findings_created=result.findings_created,
        detected_at=result.detected_at,
        sync_run_id=result.sync_run_id,
    )


@router.get("/{cloud_account_id}/findings", response_model=list[FindingRead], status_code=status.HTTP_200_OK)
def list_findings_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    db_session: Session = Depends(get_db),
) -> list[FindingRead]:
    try:
        findings = detection_service.list_findings(db_session, tenant_id, cloud_account_id)
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        elif error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise

    return [FindingRead.model_validate(item) for item in findings]


@router.post("/{cloud_account_id}/recommend/rds", response_model=RecommendationRunRead, status_code=status.HTTP_200_OK)
def recommend_rds_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    sync_run_id: UUID | None = Query(default=None),
    db_session: Session = Depends(get_db),
) -> RecommendationRunRead:
    try:
        result = recommendation_service.generate_rds_recommendations(
            db_session, tenant_id, cloud_account_id, sync_run_id=sync_run_id
        )
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        elif error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise
    except Exception:
        logger.exception(
            "RDS recommendation generation failed at API layer",
            extra={"tenant_id": str(tenant_id), "cloud_account_id": str(cloud_account_id)},
        )
        raise

    return RecommendationRunRead(
        cloud_account_id=result.cloud_account_id,
        recommendations_created=result.recommendations_created,
        created_at=result.created_at,
    )


@router.post("/{cloud_account_id}/recommend/lambda", response_model=RecommendationRunRead, status_code=status.HTTP_200_OK)
def recommend_lambda_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    sync_run_id: UUID | None = Query(default=None),
    db_session: Session = Depends(get_db),
) -> RecommendationRunRead:
    try:
        result = recommendation_service.generate_lambda_recommendations(
            db_session, tenant_id, cloud_account_id, sync_run_id=sync_run_id
        )
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        elif error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise
    except Exception:
        logger.exception(
            "Lambda recommendation generation failed at API layer",
            extra={"tenant_id": str(tenant_id), "cloud_account_id": str(cloud_account_id)},
        )
        raise

    return RecommendationRunRead(
        cloud_account_id=result.cloud_account_id,
        recommendations_created=result.recommendations_created,
        created_at=result.created_at,
    )


@router.post("/{cloud_account_id}/ingest/s3", response_model=ResourceIngestionRead, status_code=status.HTTP_200_OK)
def ingest_s3_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    db_session: Session = Depends(get_db),
) -> ResourceIngestionRead:
    try:
        result = ingestion_service.ingest_s3(db_session, tenant_id, cloud_account_id)
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        elif error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        error_type = "aws_authorization_error" if error_code in _AWS_AUTH_ERROR_CODES else "aws_client_error"
        logger.exception(
            "AWS ClientError during S3 ingestion",
            extra={"tenant_id": str(tenant_id), "cloud_account_id": str(cloud_account_id), "aws_error_code": error_code},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error_type": error_type, "aws_error_code": error_code, "message": exc.response["Error"]["Message"]},
        ) from exc
    except Exception:
        logger.exception(
            "S3 ingestion failed at API layer",
            extra={"tenant_id": str(tenant_id), "cloud_account_id": str(cloud_account_id)},
        )
        raise

    return ResourceIngestionRead(
        cloud_account_id=result.cloud_account_id,
        resource_type=result.resource_type,
        ingested_count=result.ingested_count,
        captured_at=result.captured_at,
    )


@router.post("/{cloud_account_id}/detect/s3", response_model=DetectionRunRead, status_code=status.HTTP_200_OK)
def detect_s3_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    db_session: Session = Depends(get_db),
) -> DetectionRunRead:
    sync_run_id = uuid4()
    try:
        result = detection_service.detect_s3_findings(
            db_session, tenant_id, cloud_account_id, sync_run_id
        )
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        elif error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise

    return DetectionRunRead(
        cloud_account_id=result.cloud_account_id,
        resource_type=result.resource_type,
        findings_created=result.findings_created,
        detected_at=result.detected_at,
        sync_run_id=result.sync_run_id,
    )


@router.post("/{cloud_account_id}/recommend/s3", response_model=RecommendationRunRead, status_code=status.HTTP_200_OK)
def recommend_s3_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    sync_run_id: UUID | None = Query(default=None),
    db_session: Session = Depends(get_db),
) -> RecommendationRunRead:
    try:
        result = recommendation_service.generate_s3_recommendations(
            db_session, tenant_id, cloud_account_id, sync_run_id=sync_run_id
        )
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        elif error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise
    except Exception:
        logger.exception(
            "S3 recommendation generation failed at API layer",
            extra={"tenant_id": str(tenant_id), "cloud_account_id": str(cloud_account_id)},
        )
        raise

    return RecommendationRunRead(
        cloud_account_id=result.cloud_account_id,
        recommendations_created=result.recommendations_created,
        created_at=result.created_at,
    )


@router.get("/{cloud_account_id}/recommendations", response_model=list[RecommendationRead], status_code=status.HTTP_200_OK)
def list_recommendations_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    latest_only: bool = Query(default=True),
    db_session: Session = Depends(get_db),
) -> list[RecommendationRead]:
    try:
        recommendations = recommendation_service.list_recommendation_reads(
            db_session,
            tenant_id,
            cloud_account_id,
            latest_only=latest_only,
        )
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        elif error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise

    return recommendations


@router.get("/{cloud_account_id}/recommendations/top", response_model=list[TopOpportunityRead], status_code=status.HTTP_200_OK)
def top_opportunities_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    limit: int = Query(default=5, ge=1, le=100),
    exclude_applied: bool = Query(default=False),
    db_session: Session = Depends(get_db),
) -> list[TopOpportunityRead]:
    try:
        top_opportunities = recommendation_service.get_top_opportunities(
            db_session=db_session,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            limit=limit,
            exclude_applied=exclude_applied,
        )
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        elif error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise

    return [
        TopOpportunityRead(
            recommendation_id=opp.recommendation_id,
            resource_id=opp.resource_id,
            resource_type=opp.resource_type,
            recommendation_type=opp.recommendation_type,
            recommendation_category=opp.recommendation_category,
            summary=opp.summary,
            ai_explanation=opp.ai_explanation,
            estimated_savings=opp.estimated_savings,
            risk_level=opp.risk_level,
            confidence_score=opp.confidence_score,
            computed_score=opp.computed_score,
            normalized_savings=opp.normalized_savings,
            risk_factor=opp.risk_factor,
            confidence_factor=opp.confidence_factor,
            urgency_factor=opp.urgency_factor,
            ranking_reason=opp.ranking_reason,
            priority_bucket=opp.priority_bucket,
            savings_basis=opp.savings_basis,
            confidence_reason=opp.confidence_reason,
            why_it_matters=opp.why_it_matters,
            learned_confidence=opp.learned_confidence,
            learned_confidence_reason=opp.learned_confidence_reason,
            historical_success_rate=opp.historical_success_rate,
            avg_realized_savings_for_type=opp.avg_realized_savings_for_type,
            steps=opp.steps,
            estimated_time=opp.estimated_time,
            difficulty=opp.difficulty,
        )
        for opp in top_opportunities
    ]


@router.get("/{cloud_account_id}/summary", response_model=SummaryRead, status_code=status.HTTP_200_OK)
def summary_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    db_session: Session = Depends(get_db),
) -> SummaryRead:
    try:
        result = summary_service.get_cloud_account_summary(db_session, tenant_id, cloud_account_id)
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        elif error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise

    return SummaryRead(
        cloud_account_id=result.cloud_account_id,
        total_estimated_monthly_savings=result.total_estimated_monthly_savings,
        total_cost=result.total_cost,
        savings_percentage=result.savings_percentage,
        total_recommendations=result.total_recommendations,
        by_category=result.by_category,
        by_severity=result.by_severity,
        cost_period_start=result.cost_period_start,
        cost_period_end=result.cost_period_end,
        top_cost_services=[
            TopCostServiceRead(service=item.service, amount=item.amount) for item in result.top_cost_services
        ],
        top_savings_opportunity=(
            SummaryRecommendationRead(
                recommendation_id=result.top_savings_opportunity.recommendation_id,
                resource_id=result.top_savings_opportunity.resource_id,
                recommendation_type=result.top_savings_opportunity.recommendation_type,
                recommendation_category=result.top_savings_opportunity.recommendation_category,
                summary=result.top_savings_opportunity.summary,
                estimated_savings=result.top_savings_opportunity.estimated_savings,
                risk_level=result.top_savings_opportunity.risk_level,
                confidence_score=result.top_savings_opportunity.confidence_score,
                learned_confidence=result.top_savings_opportunity.learned_confidence,
                learned_confidence_reason=result.top_savings_opportunity.learned_confidence_reason,
                historical_success_rate=result.top_savings_opportunity.historical_success_rate,
                avg_realized_savings_for_type=result.top_savings_opportunity.avg_realized_savings_for_type,
            )
            if result.top_savings_opportunity is not None
            else None
        ),
        top_risk_issue=(
            SummaryRecommendationRead(
                recommendation_id=result.top_risk_issue.recommendation_id,
                resource_id=result.top_risk_issue.resource_id,
                recommendation_type=result.top_risk_issue.recommendation_type,
                recommendation_category=result.top_risk_issue.recommendation_category,
                summary=result.top_risk_issue.summary,
                estimated_savings=result.top_risk_issue.estimated_savings,
                risk_level=result.top_risk_issue.risk_level,
                confidence_score=result.top_risk_issue.confidence_score,
                learned_confidence=result.top_risk_issue.learned_confidence,
                learned_confidence_reason=result.top_risk_issue.learned_confidence_reason,
                historical_success_rate=result.top_risk_issue.historical_success_rate,
                avg_realized_savings_for_type=result.top_risk_issue.avg_realized_savings_for_type,
            )
            if result.top_risk_issue is not None
            else None
        ),
        cost_window=result.cost_window,
        cost_window_label=result.cost_window_label,
        cost_metric=result.cost_metric,
    )


@router.get("/{cloud_account_id}/cost-summary", response_model=CostSummaryRead, status_code=status.HTTP_200_OK)
def cost_summary_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    db_session: Session = Depends(get_db),
) -> CostSummaryRead:
    try:
        result = cost_summary_service.get_cost_summary(db_session, tenant_id, cloud_account_id)
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        elif error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        if error_code in _AWS_AUTH_ERROR_CODES:
            error_type = "aws_authorization_error"
        elif error_code in _AWS_CE_UNAVAILABLE_ERROR_CODES:
            error_type = "aws_service_unavailable"
        else:
            error_type = "aws_client_error"
        logger.exception(
            "AWS ClientError during Cost Explorer fetch",
            extra={"tenant_id": str(tenant_id), "cloud_account_id": str(cloud_account_id), "aws_error_code": error_code},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error_type": error_type, "aws_error_code": error_code, "message": exc.response["Error"]["Message"]},
        ) from exc
    except Exception as exc:
        logger.exception(
            "Cost summary failed at API layer",
            extra={"tenant_id": str(tenant_id), "cloud_account_id": str(cloud_account_id)},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error_type": "aws_service_error", "message": str(exc)},
        ) from exc

    return CostSummaryRead(**result)


@router.get(
    "/{cloud_account_id}/trends",
    response_model=CostTrendsListRead,
    status_code=status.HTTP_200_OK,
)
def cost_trends_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    db_session: Session = Depends(get_db),
) -> CostTrendsListRead:
    try:
        rows = trend_service.detect_cost_trends(db_session, tenant_id, cloud_account_id)
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        if error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        if error_code in _AWS_AUTH_ERROR_CODES:
            error_type = "aws_authorization_error"
        elif error_code in _AWS_CE_UNAVAILABLE_ERROR_CODES:
            error_type = "aws_service_unavailable"
        else:
            error_type = "aws_client_error"
        logger.exception(
            "AWS ClientError during Cost Explorer trend fetch",
            extra={"tenant_id": str(tenant_id), "cloud_account_id": str(cloud_account_id), "aws_error_code": error_code},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error_type": error_type, "aws_error_code": error_code, "message": exc.response["Error"]["Message"]},
        ) from exc
    except Exception as exc:
        logger.exception(
            "Cost trends failed at API layer",
            extra={"tenant_id": str(tenant_id), "cloud_account_id": str(cloud_account_id)},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error_type": "aws_service_error", "message": str(exc)},
        ) from exc

    rows_sorted = sorted(rows, key=lambda r: abs(r["percent_change"]), reverse=True)
    meta = wow_ce_14d_window_fields()
    return CostTrendsListRead(
        cost_window=meta["cost_window"],
        cost_window_label=meta["cost_window_label"],
        cost_metric=meta["cost_metric"],
        trends=[CostTrendRead(**r) for r in rows_sorted],
    )


@router.get(
    "/{cloud_account_id}/cost-trends",
    response_model=CostTrendsSeriesRead,
    status_code=status.HTTP_200_OK,
)
def cost_trends_over_time_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    days: int = Query(default=30, ge=1, le=180),
    db_session: Session = Depends(get_db),
) -> CostTrendsSeriesRead:
    try:
        result = trend_service.cost_trends_over_time(db_session, tenant_id, cloud_account_id, days=days)
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        if error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error_type": "aws_client_error", "aws_error_code": error_code, "message": exc.response["Error"]["Message"]},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error_type": "aws_service_error", "message": str(exc)},
        ) from exc
    return CostTrendsSeriesRead(**result)


@router.get(
    "/{cloud_account_id}/savings-trends",
    response_model=SavingsTrendsSeriesRead,
    status_code=status.HTTP_200_OK,
)
def savings_trends_over_time_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    days: int = Query(default=30, ge=1, le=180),
    db_session: Session = Depends(get_db),
) -> SavingsTrendsSeriesRead:
    try:
        result = trend_service.savings_trends_over_time(db_session, tenant_id, cloud_account_id, days=days)
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        if error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise
    return SavingsTrendsSeriesRead(**result)


@router.get(
    "/{cloud_account_id}/insights",
    response_model=InsightsRead,
    status_code=status.HTTP_200_OK,
)
def insights_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    db_session: Session = Depends(get_db),
) -> InsightsRead:
    try:
        result = insight_narration_service.generate_insight_summary(db_session, tenant_id, cloud_account_id)
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        if error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        if error_code in _AWS_AUTH_ERROR_CODES:
            error_type = "aws_authorization_error"
        elif error_code in _AWS_CE_UNAVAILABLE_ERROR_CODES:
            error_type = "aws_service_unavailable"
        else:
            error_type = "aws_client_error"
        logger.exception(
            "AWS ClientError during insights fetch",
            extra={"tenant_id": str(tenant_id), "cloud_account_id": str(cloud_account_id), "aws_error_code": error_code},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error_type": error_type, "aws_error_code": error_code, "message": exc.response["Error"]["Message"]},
        ) from exc
    except Exception as exc:
        logger.exception(
            "Insights narration failed at API layer",
            extra={"tenant_id": str(tenant_id), "cloud_account_id": str(cloud_account_id)},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error_type": "aws_service_error", "message": str(exc)},
        ) from exc

    return InsightsRead(**result)


@router.get(
    "/{cloud_account_id}/action-plan",
    response_model=ActionPlanRead,
    status_code=status.HTTP_200_OK,
)
def action_plan_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    limit: int = Query(default=3, ge=3, le=5),
    db_session: Session = Depends(get_db),
) -> ActionPlanRead:
    try:
        items = recommendation_service.get_action_plan(
            db_session=db_session,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            limit=limit,
        )
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        if error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise
    return ActionPlanRead(items=items, **account_cost_window_fields())


@router.get(
    "/{cloud_account_id}/cost-breakdown/ec2-other",
    response_model=Ec2OtherBreakdownRead,
    status_code=status.HTTP_200_OK,
)
def ec2_other_breakdown_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    db_session: Session = Depends(get_db),
) -> Ec2OtherBreakdownRead:
    try:
        result = cost_summary_service.get_ec2_other_breakdown(db_session, tenant_id, cloud_account_id)
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        elif error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        raise
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        if error_code in _AWS_AUTH_ERROR_CODES:
            error_type = "aws_authorization_error"
        elif error_code in _AWS_CE_UNAVAILABLE_ERROR_CODES:
            error_type = "aws_service_unavailable"
        else:
            error_type = "aws_client_error"
        logger.exception(
            "AWS ClientError during EC2-Other Cost Explorer fetch",
            extra={"tenant_id": str(tenant_id), "cloud_account_id": str(cloud_account_id), "aws_error_code": error_code},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error_type": error_type, "aws_error_code": error_code, "message": exc.response["Error"]["Message"]},
        ) from exc
    except Exception as exc:
        logger.exception(
            "EC2-Other breakdown failed at API layer",
            extra={"tenant_id": str(tenant_id), "cloud_account_id": str(cloud_account_id)},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error_type": "aws_service_error", "message": str(exc)},
        ) from exc

    return Ec2OtherBreakdownRead(**result)


@router.post(
    "/{cloud_account_id}/recommendations/{recommendation_id}/simulate",
    response_model=SimulationRead,
    status_code=status.HTTP_200_OK,
)
def simulate_recommendation_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
    db_session: Session = Depends(get_db),
) -> SimulationRead:
    try:
        result = simulation_service.simulate_recommendation(
            db_session=db_session,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            recommendation_id=recommendation_id,
        )
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        elif error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        elif error_msg == "recommendation_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recommendation not found") from exc
        raise

    return SimulationRead(
        recommendation_id=result.recommendation_id,
        resource_id=result.resource_id,
        recommendation_type=result.recommendation_type,
        recommendation_category=result.recommendation_category,
        current_state=result.current_state,
        proposed_state=result.proposed_state,
        impact_summary=result.impact_summary,
        risk_reduction=result.risk_reduction,
        estimated_savings=result.estimated_savings,
        confidence_score=result.confidence_score,
    )


@router.post(
    "/{cloud_account_id}/recommendations/{recommendation_id}/outcomes",
    response_model=RecommendationOutcomeRead,
    status_code=status.HTTP_201_CREATED,
)
def create_recommendation_outcome_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
    body: RecommendationOutcomeCreate,
    db_session: Session = Depends(get_db),
) -> RecommendationOutcomeRead:
    try:
        outcome = recommendation_outcome_service.create_outcome_for_recommendation(
            db_session=db_session,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            recommendation_id=recommendation_id,
            notes=body.notes,
        )
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        if error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        if error_msg == "recommendation_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recommendation not found") from exc
        raise

    return RecommendationOutcomeRead.model_validate(outcome)


@router.post(
    "/{cloud_account_id}/recommendations/{recommendation_id}/mark-acted-on",
    response_model=RecommendationOutcomeRead,
    status_code=status.HTTP_200_OK,
)
def mark_recommendation_acted_on_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
    body: RecommendationOutcomeUpdate | None = None,
    user_ctx: UserContext = Depends(get_user_context),
    db_session: Session = Depends(get_db),
) -> RecommendationOutcomeRead:
    # Deprecated manual action: access enforced by route dependency (admin on this cloud account).
    try:
        outcome = recommendation_outcome_service.mark_recommendation_acted_on(
            db_session=db_session,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            recommendation_id=recommendation_id,
            notes=body.notes if body is not None else None,
        )
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        if error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        if error_msg == "recommendation_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recommendation not found") from exc
        raise

    return RecommendationOutcomeRead.model_validate(outcome)


@router.post(
    "/{cloud_account_id}/recommendations/{recommendation_id}/approve",
    response_model=RecommendationOutcomeRead,
    status_code=status.HTTP_200_OK,
)
def approve_recommendation_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
    body: RecommendationApprovalUpdate | None = None,
    user_ctx: UserContext = Depends(get_user_context),
    db_session: Session = Depends(get_db),
) -> RecommendationOutcomeRead:
    if approval_request_service.has_open_multi_approver_request(
        db_session, tenant_id, cloud_account_id, recommendation_id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An open multi-approver approval request exists; complete it from Approvals instead of single Approve.",
        )
    try:
        eff = access_resolution_service.resolve_effective_access(
            db_session, user_ctx, tenant_id, cloud_account_id
        )
        outcome = recommendation_outcome_service.approve_recommendation(
            db_session=db_session,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            recommendation_id=recommendation_id,
            approved_by=(user_ctx.email or (body.approved_by if body is not None else None)),
            approved_role=eff,
            approval_comment=(body.approval_comment if body is not None else None),
            approved_membership_role=access_resolution_service.org_membership_role_label(user_ctx.role),
            approved_access_role=eff,
        )
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        if error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        if error_msg == "recommendation_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recommendation not found") from exc
        raise

    return RecommendationOutcomeRead.model_validate(outcome)


@router.post(
    "/{cloud_account_id}/recommendations/{recommendation_id}/reject",
    response_model=RecommendationOutcomeRead,
    status_code=status.HTTP_200_OK,
)
def reject_recommendation_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
    body: RecommendationRejectRequest,
    user_ctx: UserContext = Depends(get_user_context),
    db_session: Session = Depends(get_db),
) -> RecommendationOutcomeRead:
    if approval_request_service.has_open_multi_approver_request(
        db_session, tenant_id, cloud_account_id, recommendation_id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An open multi-approver approval request exists; reject via the approval request instead.",
        )
    try:
        outcome = recommendation_outcome_service.reject_recommendation(
            db_session=db_session,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            recommendation_id=recommendation_id,
            rejection_reason=body.rejection_reason,
            rejected_by=user_ctx.email,
        )
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        if error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        if error_msg == "recommendation_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recommendation not found") from exc
        if error_msg == "rejection_reason_required":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="rejection_reason is required.",
            ) from exc
        if error_msg == "cannot_reject_applied_or_verified":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot reject a recommendation that is already applied or verified.",
            ) from exc
        raise

    return RecommendationOutcomeRead.model_validate(outcome)


@router.post(
    "/{cloud_account_id}/recommendations/{recommendation_id}/mark-applied",
    response_model=RecommendationOutcomeRead,
    status_code=status.HTTP_200_OK,
)
def mark_recommendation_applied_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
    body: RecommendationAppliedUpdate | None = None,
    user_ctx: UserContext = Depends(get_user_context),
    db_session: Session = Depends(get_db),
) -> RecommendationOutcomeRead:
    try:
        eff = access_resolution_service.resolve_effective_access(
            db_session, user_ctx, tenant_id, cloud_account_id
        )
        outcome = recommendation_outcome_service.mark_recommendation_applied(
            db_session=db_session,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            recommendation_id=recommendation_id,
            applied_by=user_ctx.email if user_ctx.email else (body.applied_by if body is not None else None),
            applied_role=eff,
            execution_notes=body.execution_notes if body is not None else None,
            applied_membership_role=access_resolution_service.org_membership_role_label(user_ctx.role),
            applied_access_role=eff,
        )
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        if error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        if error_msg == "recommendation_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recommendation not found") from exc
        raise

    return RecommendationOutcomeRead.model_validate(outcome)


@router.post(
    "/{cloud_account_id}/recommendations/{recommendation_id}/execute",
    response_model=RecommendationExecuteRead,
    status_code=status.HTTP_200_OK,
)
def execute_recommendation_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
    body: RecommendationExecuteRequest,
    user_ctx: UserContext = Depends(get_user_context),
    db_session: Session = Depends(get_db),
) -> RecommendationExecuteRead:
    try:
        eff = access_resolution_service.resolve_effective_access(
            db_session, user_ctx, tenant_id, cloud_account_id
        )
        result = safe_execution_service.execute_recommendation(
            db_session=db_session,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            recommendation_id=recommendation_id,
            executed_by=(user_ctx.email or body.executed_by),
            executed_role=eff,
            tag_values=body.tag_values,
            applied_membership_role=access_resolution_service.org_membership_role_label(user_ctx.role),
            applied_access_role=eff,
        )
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        if error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        if error_msg == "recommendation_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recommendation not found") from exc
        if error_msg == "workflow_rejected":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This recommendation was rejected and cannot be executed.",
            ) from exc
        if error_msg == "workflow_not_approved":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Recommendation must be approved first") from exc
        if error_msg == "approval_request_pending":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Waiting for all required approvals on the multi-approver request before execution.",
            ) from exc
        if error_msg == "approval_request_rejected":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This recommendation was rejected in the multi-approver approval flow.",
            ) from exc
        if error_msg == "unsupported_recommendation_type":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Recommendation type is not in safe execution allowlist") from exc
        if error_msg == "preflight_required_not_passed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Policy requires a successful preflight before execution. Open the recommendation to run preflight, then try again.",
            ) from exc
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found.") from exc
        raise
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error_type": "aws_client_error", "aws_error_code": error_code, "message": exc.response["Error"]["Message"]},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error_type": "aws_service_error", "message": str(exc)},
        ) from exc

    return RecommendationExecuteRead(**result)


@router.get(
    "/{cloud_account_id}/recommendations/{recommendation_id}/preflight",
    response_model=RecommendationPreflightRead,
    status_code=status.HTTP_200_OK,
)
def preflight_recommendation_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
    db_session: Session = Depends(get_db),
) -> RecommendationPreflightRead:
    try:
        result = preflight_dry_run_service.run_preflight(
            db_session=db_session,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            recommendation_id=recommendation_id,
        )
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        if error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        if error_msg == "recommendation_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recommendation not found") from exc
        raise
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error_type": "aws_client_error", "aws_error_code": error_code, "message": exc.response["Error"]["Message"]},
        ) from exc

    recommendation_outcome_service.record_preflight_passed_if_ready(
        db_session,
        tenant_id,
        cloud_account_id,
        recommendation_id,
        aggregate_status=str(result.get("status") or ""),
    )

    return RecommendationPreflightRead(**result)


@router.get(
    "/{cloud_account_id}/recommendations/{recommendation_id}/dry-run",
    response_model=RecommendationDryRunRead,
    status_code=status.HTTP_200_OK,
)
def dry_run_recommendation_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
    db_session: Session = Depends(get_db),
    tag_name: str | None = Query(None, description="Optional Name tag for s3_add_required_tags preview"),
    tag_environment: str | None = Query(None, description="Optional Environment tag for s3_add_required_tags preview"),
) -> RecommendationDryRunRead:
    tag_values: dict[str, str] | None = None
    if tag_name is not None or tag_environment is not None:
        tag_values = {}
        if tag_name is not None:
            tag_values["Name"] = tag_name
        if tag_environment is not None:
            tag_values["Environment"] = tag_environment

    try:
        result = preflight_dry_run_service.run_dry_run(
            db_session=db_session,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            recommendation_id=recommendation_id,
            tag_values=tag_values,
        )
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found") from exc
        if error_msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found") from exc
        if error_msg == "recommendation_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recommendation not found") from exc
        raise
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error_type": "aws_client_error", "aws_error_code": error_code, "message": exc.response["Error"]["Message"]},
        ) from exc

    return RecommendationDryRunRead(**result)


@router.get(
    "/{cloud_account_id}/outcomes",
    response_model=list[RecommendationOutcomeRead],
    status_code=status.HTTP_200_OK,
)
def list_recommendation_outcomes_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    db_session: Session = Depends(get_db),
) -> list[RecommendationOutcomeRead]:
    outcomes = recommendation_outcome_service.list_outcomes(
        db_session=db_session,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
    )

    # Recompute current cost + realized savings on read to support manual before/after comparisons.
    recomputed: list = []
    for outcome in outcomes:
        try:
            recomputed.append(
                recommendation_outcome_service.recompute_realized_savings(
                    db_session=db_session,
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    outcome=outcome,
                )
            )
        except ValueError:
            # If cloud/tenant is missing, propagate as a 404.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant/Cloud account not found")

    return [RecommendationOutcomeRead.model_validate(o) for o in recomputed]


@router.get(
    "/{cloud_account_id}/workflow",
    response_model=list[RecommendationWorkflowRead],
    status_code=status.HTTP_200_OK,
)
def list_workflow_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    db_session: Session = Depends(get_db),
) -> list[RecommendationWorkflowRead]:
    rows = recommendation_outcome_service.list_workflow_rows(
        db_session=db_session,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
    )
    return [RecommendationWorkflowRead(**r) for r in rows]


@router.get(
    "/{cloud_account_id}/workflow/timeline",
    response_model=list[RecommendationWorkflowTimelineRead],
    status_code=status.HTTP_200_OK,
)
def list_workflow_timeline_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    db_session: Session = Depends(get_db),
) -> list[RecommendationWorkflowTimelineRead]:
    rows = recommendation_outcome_service.list_workflow_timeline(
        db_session=db_session,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
    )
    return [RecommendationWorkflowTimelineRead(**r) for r in rows]


@router.get(
    "/{cloud_account_id}/workflow/progress",
    response_model=RecommendationWorkflowProgressRead,
    status_code=status.HTTP_200_OK,
)
def workflow_progress_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    db_session: Session = Depends(get_db),
) -> RecommendationWorkflowProgressRead:
    result = recommendation_outcome_service.workflow_progress_summary(
        db_session=db_session,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
    )
    return RecommendationWorkflowProgressRead(**result)


@router.get(
    "/{cloud_account_id}/savings-proof/summary",
    response_model=SavingsProofSummaryRead,
    status_code=status.HTTP_200_OK,
)
def savings_proof_summary_cloud_account_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    db_session: Session = Depends(get_db),
) -> SavingsProofSummaryRead:
    """Total estimated savings from rolling 30d account before/after snapshots for this cloud account."""
    result = recommendation_outcome_service.savings_proof_summary_for_cloud_account(
        db_session, tenant_id, cloud_account_id
    )
    return SavingsProofSummaryRead(**result)


@router.post(
    "/{cloud_account_id}/outcomes/{outcome_id}/analyze",
    response_model=RecommendationOutcomeRead,
    status_code=status.HTTP_200_OK,
)
def analyze_recommendation_outcome_endpoint(
    tenant_id: UUID,
    cloud_account_id: UUID,
    outcome_id: UUID,
    db_session: Session = Depends(get_db),
) -> RecommendationOutcomeRead:
    try:
        outcome = outcome_impact_service.analyze_outcome_by_id(
            db_session=db_session,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            outcome_id=outcome_id,
        )
    except ValueError as exc:
        error_msg = str(exc)
        if error_msg == "outcome_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Outcome not found") from exc
        if error_msg == "outcome_not_actionable":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Outcome must be acted on or verified before analysis",
            ) from exc
        raise

    return RecommendationOutcomeRead.model_validate(outcome)

