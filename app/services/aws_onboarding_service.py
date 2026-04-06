"""AWS CloudFormation self-service onboarding service.

Handles the full lifecycle for a customer connecting their AWS account
via CloudFormation-deployed cross-account IAM roles:

  1. start()        — create CloudAccount skeleton, generate external_id + onboarding_token
  2. cfn_params()   — build the CF launch URL + safe parameters for the UI
  3. confirm_roles()— customer submits role ARNs after stack completes
  4. validate()     — AssumeRole test both roles, mark statuses, kick off first sync
  5. get()          — read-back for polling

Role naming convention (customer-side):
  FptNextReadOnlyRole   — attached arn:aws:iam::aws:policy/ReadOnlyAccess + narrowed deny list
  FptNextExecutionRole  — restricted to resource tagging only
"""

from __future__ import annotations

import logging
import re
import secrets
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.aws_field_validation import validate_role_arn
from app.core.config import MEEZI_PLATFORM_AWS_ACCOUNT_ID, settings
from app.core.db import utc_now
from app.models.cloud_account import CloudAccount
from app.schemas.aws_onboarding import (
    CfnLaunchParams,
    OnboardingRead,
    OnboardingStartRequest,
    OnboardingValidationResult,
)
from app.services import aws_validation_service
from app.services.cloud_account_service import _get_tenant_or_raise

logger = logging.getLogger(__name__)

# ── Role name constants ────────────────────────────────────────────────────────
READ_ONLY_ROLE_NAME = "FptNextReadOnlyRole"
EXECUTION_ROLE_NAME = "FptNextExecutionRole"

# ── Statuses ───────────────────────────────────────────────────────────────────
_RO_PENDING = "pending"
_RO_AWAITING = "awaiting_role_arn"
_RO_VALIDATING = "validating"
_RO_CONNECTED = "connected"
_RO_FAILED = "failed"

_EX_NOT_CONFIGURED = "not_configured"
_EX_AWAITING = "awaiting_role_arn"
_EX_VALIDATING = "validating"
_EX_CONNECTED = "connected"
_EX_FAILED = "failed"

# ── CloudFormation launch URL base ─────────────────────────────────────────────
_CFN_LAUNCH_BASE = (
    "https://console.aws.amazon.com/cloudformation/home"
    "?region=us-east-1#/stacks/create/review"
)

# ── Onboarding token ───────────────────────────────────────────────────────────
_TOKEN_BYTES = 32  # 256-bit URL-safe token


def _generate_external_id() -> str:
    """Generate a cryptographically random external ID (URL-safe, 40 chars)."""
    return secrets.token_urlsafe(30)


def _generate_onboarding_token() -> str:
    """Generate a short-lived invite token for the shareable onboarding URL."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def _placeholder_account_id() -> str:
    """Return a unique placeholder 12-char hex to satisfy the (tenant_id, account_id) unique constraint.

    The placeholder is replaced with the real AWS account ID when the customer confirms the role ARN.
    The role_arn column separately stores '000000000000' to signal an unconfirmed state.
    """
    return secrets.token_hex(6)  # 6 random bytes → 12 lowercase hex chars


# ── CF template URL ────────────────────────────────────────────────────────────

def _template_url() -> str:
    """Return the public S3 URL where the CF template YAML is hosted.

    AWS CloudFormation's quick-create console only accepts S3 URLs for
    templateURL; non-S3 URLs are rejected and users are forced to re-enter
    the URL manually.  The base URL must therefore always point to S3 (or a
    compatible CDN), which is enforced by the MEEZI_CFN_TEMPLATE_S3_BASE_URL
    default in settings.
    """
    base = settings.cf_template_base_url.rstrip("/")
    return f"{base}/fptnext-roles.yaml"


# ── CFN launch URL builder ─────────────────────────────────────────────────────

def _build_cfn_launch_url(
    template_url: str,
    external_id: str,
    platform_account_id: str,
    include_execution_role: bool,
) -> str:
    """Assemble a CloudFormation Quick-create launch URL."""
    import urllib.parse

    params: dict[str, str] = {
        "templateURL": template_url,
        "stackName": "FptNextRoles",
        "param_PlatformAwsAccountId": platform_account_id,
        "param_ExternalId": external_id,
        "param_ReadOnlyRoleName": READ_ONLY_ROLE_NAME,
        "param_ExecutionRoleName": EXECUTION_ROLE_NAME,
        "param_IncludeExecutionRole": "true" if include_execution_role else "false",
    }
    qs = urllib.parse.urlencode(params)
    return f"{_CFN_LAUNCH_BASE}?{qs}"


# ── CRUD ───────────────────────────────────────────────────────────────────────

def start_onboarding(
    db: Session,
    *,
    tenant_id: UUID,
    data: OnboardingStartRequest,
    user_id: UUID | None = None,
) -> CloudAccount:
    """Create a new CloudAccount skeleton for the CF onboarding flow.

    Generates a fresh external_id and onboarding_token.
    The role_arn is left as a placeholder until the customer confirms.
    """
    _get_tenant_or_raise(db, tenant_id)

    from sqlalchemy.exc import IntegrityError

    external_id = _generate_external_id()
    token = _generate_onboarding_token()
    include_exec = data.onboarding_mode == "read_and_execution"

    row = CloudAccount(
        tenant_id=tenant_id,
        # Placeholder — will be filled in from the confirmed ARN.
        account_id=_placeholder_account_id(),
        name=data.name,
        status="pending",
        connection_status="untested",
        # role_arn must be non-null per schema; use placeholder ARN (never called until confirmed).
        role_arn=f"arn:aws:iam::000000000000:role/{READ_ONLY_ROLE_NAME}",
        execution_role_arn=None,
        external_id=external_id,
        region_default=data.region_default,
        # CF onboarding fields
        onboarding_mode=data.onboarding_mode,
        read_only_status=_RO_PENDING,
        execution_status=_EX_AWAITING if include_exec else _EX_NOT_CONFIGURED,
        cf_stack_launched_at=None,
        created_by_user_id=user_id,
        onboarding_token=token,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise
    db.refresh(row)
    return row


def get_onboarding_by_id(
    db: Session,
    record_id: UUID,
) -> CloudAccount | None:
    return db.query(CloudAccount).filter(CloudAccount.id == record_id).first()


def get_onboarding_by_token(
    db: Session,
    token: str,
) -> CloudAccount | None:
    return (
        db.query(CloudAccount)
        .filter(CloudAccount.onboarding_token == token)
        .first()
    )


def confirm_roles(
    db: Session,
    record: CloudAccount,
    *,
    read_only_role_arn: str,
    execution_role_arn: str | None,
) -> CloudAccount:
    """Store the customer-provided ARNs and advance status to validating."""
    from app.core.aws_field_validation import account_id_from_role_arn
    from sqlalchemy.exc import IntegrityError

    # Derive aws account_id from the ARN
    aws_acct = account_id_from_role_arn(read_only_role_arn)
    if aws_acct:
        # If another record already owns this (tenant_id, account_id) pair (e.g. a
        # stale/failed onboarding attempt), remove it so the active session can proceed.
        conflict = (
            db.query(CloudAccount)
            .filter(
                CloudAccount.tenant_id == record.tenant_id,
                CloudAccount.account_id == aws_acct,
                CloudAccount.id != record.id,
            )
            .first()
        )
        if conflict:
            logger.warning(
                "confirm_roles: removing stale cloud_account %s (account_id=%s) "
                "to allow re-onboarding for tenant %s",
                conflict.id, aws_acct, record.tenant_id,
            )
            db.delete(conflict)
            db.flush()
        record.account_id = aws_acct

    record.role_arn = read_only_role_arn
    record.execution_role_arn = execution_role_arn
    record.read_only_status = _RO_VALIDATING
    if execution_role_arn:
        record.execution_status = _EX_VALIDATING
    elif record.onboarding_mode == "read_and_execution":
        # User chose read+exec but didn't submit exec ARN yet
        record.execution_status = _EX_AWAITING
    record.updated_at = utc_now()
    db.commit()
    db.refresh(record)
    return record


def mark_cf_launched(db: Session, record: CloudAccount) -> CloudAccount:
    """Record that the customer clicked the Launch CloudFormation button."""
    record.cf_stack_launched_at = utc_now()
    record.read_only_status = _RO_AWAITING
    if record.execution_status == _EX_NOT_CONFIGURED:
        pass  # leave as-is
    elif record.execution_status == _RO_PENDING:
        record.execution_status = _EX_AWAITING
    record.updated_at = utc_now()
    db.commit()
    db.refresh(record)
    return record


def validate_onboarding(
    db: Session,
    record: CloudAccount,
) -> OnboardingValidationResult:
    """Run STS AssumeRole validation for both roles and update record status."""
    read_only_validated = False
    execution_validated: bool | None = None
    read_only_error: str | None = None
    execution_error: str | None = None
    aws_account_id: str | None = None

    # ── Validate read-only role ────────────────────────────────────────────────
    try:
        result = aws_validation_service.validate_cloud_account_role(
            role_arn=record.role_arn,
            region=record.region_default,
            external_id=record.external_id,
        )
        if result.success:
            read_only_validated = True
            aws_account_id = result.aws_account_id
            record.read_only_status = _RO_CONNECTED
            record.connection_status = "valid"
            record.last_validated_at = utc_now()
            record.last_validation_error = None
            logger.info(
                "Onboarding read-only validation success",
                extra={"record_id": str(record.id), "aws_account": aws_account_id},
            )
        else:
            record.read_only_status = _RO_FAILED
            record.connection_status = "invalid"
            record.last_validation_error = result.error_message
            read_only_error = _user_error_message(result.error_message)
            logger.warning(
                "Onboarding read-only validation failed",
                extra={"record_id": str(record.id), "error": result.error_message},
            )
    except Exception as exc:
        record.read_only_status = _RO_FAILED
        record.connection_status = "invalid"
        record.last_validation_error = str(exc)
        read_only_error = "Could not connect to AWS. Check the role ARN and trust policy."
        logger.exception("Onboarding read-only validation exception", extra={"record_id": str(record.id)})

    # ── Validate execution role (if configured) ────────────────────────────────
    if record.execution_role_arn and record.execution_role_arn.strip():
        try:
            ex_result = aws_validation_service.validate_cloud_account_role(
                role_arn=record.execution_role_arn,
                region=record.region_default,
                external_id=record.external_id,
            )
            if ex_result.success:
                execution_validated = True
                record.execution_status = _EX_CONNECTED
                logger.info(
                    "Onboarding execution validation success",
                    extra={"record_id": str(record.id)},
                )
            else:
                execution_validated = False
                record.execution_status = _EX_FAILED
                execution_error = _user_error_message(ex_result.error_message)
                logger.warning(
                    "Onboarding execution validation failed",
                    extra={"record_id": str(record.id), "error": ex_result.error_message},
                )
        except Exception as exc:
            execution_validated = False
            record.execution_status = _EX_FAILED
            execution_error = "Could not validate execution role. Check the ARN and trust policy."
            logger.exception("Onboarding execution validation exception", extra={"record_id": str(record.id)})

    db.commit()
    db.refresh(record)

    return OnboardingValidationResult(
        read_only_validated=read_only_validated,
        execution_validated=execution_validated,
        read_only_error=read_only_error,
        execution_error=execution_error,
        aws_account_id=aws_account_id,
    )


def build_cfn_launch_params(record: CloudAccount) -> CfnLaunchParams:
    """Build the CloudFormation launch URL and parameter set for the onboarding UI."""
    platform_acct = (settings.platform_aws_account_id or "").strip() or MEEZI_PLATFORM_AWS_ACCOUNT_ID
    tpl_url = _template_url()
    include_exec = record.onboarding_mode == "read_and_execution"

    launch_url = _build_cfn_launch_url(
        template_url=tpl_url,
        external_id=record.external_id or "",
        platform_account_id=platform_acct,
        include_execution_role=include_exec,
    )

    return CfnLaunchParams(
        platform_aws_account_id=platform_acct,
        external_id=record.external_id or "",
        read_only_role_name=READ_ONLY_ROLE_NAME,
        execution_role_name=EXECUTION_ROLE_NAME,
        include_execution_role=include_exec,
        cfn_launch_url=launch_url,
        template_url=tpl_url,
    )


def to_read(record: CloudAccount) -> OnboardingRead:
    cfn = build_cfn_launch_params(record)
    # Only expose the ARN if it's been confirmed (not the placeholder)
    role_arn = record.role_arn if (record.role_arn and "000000000000" not in record.role_arn) else None
    return OnboardingRead(
        id=record.id,
        tenant_id=record.tenant_id,
        name=record.name,
        region_default=record.region_default,
        onboarding_mode=record.onboarding_mode,
        read_only_status=record.read_only_status,
        execution_status=record.execution_status,
        external_id=record.external_id,
        role_arn=role_arn,
        execution_role_arn=record.execution_role_arn,
        connection_status=record.connection_status,
        cf_stack_launched_at=record.cf_stack_launched_at,
        last_validated_at=record.last_validated_at,
        last_validation_error=record.last_validation_error,
        onboarding_token=record.onboarding_token,
        cfn_launch=cfn,
    )


# ── Error message sanitizer ────────────────────────────────────────────────────

def _user_error_message(raw: str | None) -> str:
    """Convert an AWS error message to a short, customer-safe string."""
    if not raw:
        return "Unknown error from AWS."
    r = raw.strip()
    # AccessDenied / not authorized — trust policy issue
    if "AccessDenied" in r or "not authorized" in r.lower():
        return (
            "AWS denied access. Ensure the trust policy includes "
            "the platform account ID and the correct External ID. "
            "Wait for CloudFormation to complete before validating."
        )
    # No such role
    if "NoSuchEntity" in r or "does not exist" in r.lower():
        return (
            "Role not found. Confirm the CloudFormation stack created successfully "
            "and that you submitted the correct ARN shown in the Outputs tab."
        )
    # Safe truncation
    return r[:300]
