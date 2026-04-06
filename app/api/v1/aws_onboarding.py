"""AWS CloudFormation self-service onboarding API.

Endpoints:
  POST   /onboarding/start                — create session, get external_id + CF launch URL
  GET    /onboarding/{id}                 — poll status (auth or token)
  GET    /onboarding/by-token/{token}     — public shareable link (no login needed)
  POST   /onboarding/{id}/cf-launched     — record that customer clicked Launch CF
  PATCH  /onboarding/{id}/confirm-roles   — submit role ARNs after stack completes
  POST   /onboarding/{id}/validate        — run AssumeRole validation + kick sync
  GET    /onboarding/cfn-template         — serve the YAML template (public)
"""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.user_context import UserContext, get_user_context
from app.core.config import settings
from app.schemas.aws_onboarding import (
    OnboardingConfirmRolesRequest,
    OnboardingInviteRequest,
    OnboardingInviteResponse,
    OnboardingRead,
    OnboardingStartRequest,
    OnboardingValidationResult,
)
from app.services import aws_onboarding_service, ingestion_job_service, invite_email_service, tenant_scope_service, tenant_service
from app.services.ingestion_job_service import ActiveSyncJobExists

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

_CFN_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[4]
    / "infrastructure"
    / "cloudformation"
    / "fptnext-roles.yaml"
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_record_or_404(db: Session, record_id: UUID):
    record = aws_onboarding_service.get_onboarding_by_id(db, record_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Onboarding session not found.")
    return record


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post(
    "/start",
    response_model=OnboardingRead,
    status_code=status.HTTP_201_CREATED,
    summary="Start a new AWS CloudFormation onboarding session",
)
def start_onboarding(
    body: OnboardingStartRequest,
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> OnboardingRead:
    """Create a CloudAccount skeleton, generate external_id and onboarding token.

    Returns the CloudFormation launch parameters so the UI can immediately
    show the Launch CloudFormation button.
    """
    # Must be org admin or above to initiate onboarding
    tenant_scope_service.require_tenant_write_role(ctx)

    try:
        record = aws_onboarding_service.start_onboarding(
            db,
            tenant_id=body.tenant_id,
            data=body,
            user_id=ctx.user_id,
        )
    except ValueError as exc:
        msg = str(exc)
        if msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found.") from exc
        raise

    return aws_onboarding_service.to_read(record)


@router.post(
    "/invite",
    response_model=OnboardingInviteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an onboarding session and email the customer a shareable connect link",
)
def send_onboarding_invite(
    body: OnboardingInviteRequest,
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> OnboardingInviteResponse:
    """Creates a CloudFormation onboarding session for the given tenant and sends the
    customer an email with a one-click link to /connect/aws?token=<token>.

    The email mimics the user invitation email (same template, branded MEEZI header).
    Requires org_admin or above.
    """
    tenant_scope_service.require_tenant_write_role(ctx)

    # Build a start-request from the invite body
    start_req = OnboardingStartRequest(
        tenant_id=body.tenant_id,
        name=body.account_name,
        region_default=body.region_default,
        onboarding_mode=body.onboarding_mode,
    )

    try:
        record = aws_onboarding_service.start_onboarding(
            db,
            tenant_id=body.tenant_id,
            data=start_req,
            user_id=ctx.user_id,
        )
    except ValueError as exc:
        msg = str(exc)
        if msg == "tenant_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found.") from exc
        raise

    frontend_base = (settings.frontend_url or "http://localhost:5173").rstrip("/")
    connect_url = f"{frontend_base}/connect/aws?token={record.onboarding_token}"

    # Optionally enrich the email with org/tenant name
    tenant = tenant_service.get_tenant(db, body.tenant_id)
    tenant_name = getattr(tenant, "name", None)
    org = getattr(tenant, "organization", None)
    org_name = getattr(org, "name", None) if org else None

    email_sent = False
    try:
        email_sent = invite_email_service.send_aws_connect_invite_email(
            recipient_email=body.email,
            connect_url=connect_url,
            org_name=org_name,
            tenant_name=tenant_name,
        )
    except Exception:
        logger.exception("Failed to send AWS connect invite email. Returning session anyway.")

    return OnboardingInviteResponse(
        session=aws_onboarding_service.to_read(record),
        connect_url=connect_url,
        email_sent=email_sent,
    )


@router.get(
    "/cfn-template",
    response_class=PlainTextResponse,
    summary="Download the CloudFormation template YAML",
)
def get_cfn_template() -> str:
    """Returns the YAML CloudFormation template for customers to review or host."""
    if not _CFN_TEMPLATE_PATH.exists():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CloudFormation template is not available on this server.",
        )
    return _CFN_TEMPLATE_PATH.read_text(encoding="utf-8")


@router.get(
    "/by-token/{token}",
    response_model=OnboardingRead,
    summary="Access onboarding session via shareable invite token (public)",
)
def get_onboarding_by_token(
    token: str,
    db: Session = Depends(get_db),
) -> OnboardingRead:
    """Public endpoint — no login required if the caller has a valid onboarding token."""
    record = aws_onboarding_service.get_onboarding_by_token(db, token)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Onboarding session not found or token is invalid.",
        )
    return aws_onboarding_service.to_read(record)


@router.get(
    "/{onboarding_id}",
    response_model=OnboardingRead,
    summary="Get onboarding session status",
)
def get_onboarding(
    onboarding_id: UUID,
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> OnboardingRead:
    record = _get_record_or_404(db, onboarding_id)
    return aws_onboarding_service.to_read(record)


@router.post(
    "/{onboarding_id}/cf-launched",
    response_model=OnboardingRead,
    summary="Record that the customer clicked 'Launch CloudFormation'",
)
def mark_cf_launched(
    onboarding_id: UUID,
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> OnboardingRead:
    record = _get_record_or_404(db, onboarding_id)
    record = aws_onboarding_service.mark_cf_launched(db, record)
    return aws_onboarding_service.to_read(record)


@router.patch(
    "/{onboarding_id}/confirm-roles",
    response_model=OnboardingRead,
    summary="Submit role ARNs from the CloudFormation stack outputs",
)
def confirm_roles(
    onboarding_id: UUID,
    body: OnboardingConfirmRolesRequest,
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> OnboardingRead:
    """Customer pastes in the ReadOnlyRoleArn (and optionally ExecutionRoleArn).

    ARN format is validated before the record is updated, and status moves
    to 'validating'.  Call POST /{id}/validate next.
    """
    record = _get_record_or_404(db, onboarding_id)
    record = aws_onboarding_service.confirm_roles(
        db,
        record,
        read_only_role_arn=body.read_only_role_arn,
        execution_role_arn=body.execution_role_arn,
    )
    return aws_onboarding_service.to_read(record)


@router.post(
    "/{onboarding_id}/validate",
    response_model=OnboardingValidationResult,
    summary="Run AssumeRole validation and kick off first sync on success",
)
def validate_onboarding(
    onboarding_id: UUID,
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> OnboardingValidationResult:
    """Assume both configured roles via STS and verify credentials.

    On read-only success: marks connection_status=valid and queues the first
    full_sync ingestion job (non-blocking — returns immediately).
    On failure: stores the error and returns guidance.
    """
    record = _get_record_or_404(db, onboarding_id)

    if not record.role_arn or "000000000000" in record.role_arn:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Role ARNs must be submitted via /confirm-roles before validating.",
        )

    result = aws_onboarding_service.validate_onboarding(db, record)

    # ── Kick off first sync if read-only validation succeeded ─────────────────
    if result.read_only_validated:
        try:
            ingestion_job_service.create_sync_job(
                db_session=db,
                tenant_id=record.tenant_id,
                cloud_account_id=record.id,
                job_type="full_sync",
            )
            logger.info(
                "Onboarding: first sync queued",
                extra={"record_id": str(record.id), "tenant_id": str(record.tenant_id)},
            )
        except ActiveSyncJobExists:
            # Already in progress — not an error
            logger.debug("Onboarding: sync already active for %s", record.id)
        except Exception:
            # Do not fail the validate call if sync kick-off fails
            logger.exception("Onboarding: could not queue first sync for %s", record.id)

    return result


# ── Token-based confirm/validate routes (shareable link, no login) ─────────────

@router.post(
    "/by-token/{token}/cf-launched",
    response_model=OnboardingRead,
    summary="(Token) Record CF launched",
)
def token_mark_cf_launched(
    token: str,
    db: Session = Depends(get_db),
) -> OnboardingRead:
    record = aws_onboarding_service.get_onboarding_by_token(db, token)
    if record is None:
        raise HTTPException(status_code=404, detail="Token not found.")
    record = aws_onboarding_service.mark_cf_launched(db, record)
    return aws_onboarding_service.to_read(record)


@router.patch(
    "/by-token/{token}/confirm-roles",
    response_model=OnboardingRead,
    summary="(Token) Submit role ARNs",
)
def token_confirm_roles(
    token: str,
    body: OnboardingConfirmRolesRequest,
    db: Session = Depends(get_db),
) -> OnboardingRead:
    record = aws_onboarding_service.get_onboarding_by_token(db, token)
    if record is None:
        raise HTTPException(status_code=404, detail="Token not found.")
    record = aws_onboarding_service.confirm_roles(
        db, record,
        read_only_role_arn=body.read_only_role_arn,
        execution_role_arn=body.execution_role_arn,
    )
    return aws_onboarding_service.to_read(record)


@router.post(
    "/by-token/{token}/validate",
    response_model=OnboardingValidationResult,
    summary="(Token) Run validation + first sync",
)
def token_validate_onboarding(
    token: str,
    db: Session = Depends(get_db),
) -> OnboardingValidationResult:
    record = aws_onboarding_service.get_onboarding_by_token(db, token)
    if record is None:
        raise HTTPException(status_code=404, detail="Token not found.")

    if not record.role_arn or "000000000000" in record.role_arn:
        raise HTTPException(
            status_code=422,
            detail="Role ARNs must be submitted before validating.",
        )

    result = aws_onboarding_service.validate_onboarding(db, record)

    if result.read_only_validated:
        try:
            ingestion_job_service.create_sync_job(
                db_session=db,
                tenant_id=record.tenant_id,
                cloud_account_id=record.id,
                job_type="full_sync",
            )
        except ActiveSyncJobExists:
            pass
        except Exception:
            logger.exception("Onboarding(token): could not queue first sync for %s", record.id)

    return result
