from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.cloud_account import CloudAccount
from app.models.tenant import Tenant
from app.core.aws_field_validation import normalize_aws_account_id
from app.core.db import utc_now
from app.schemas.cloud_account import CloudAccountCreate
from app.services import aws_validation_service
from app.services.tenant_service import (
    ARCHIVED_TIPWAVE_LEGACY_TENANT_NAME,
    TIPWAVE_CLOUD_ACCOUNT_ROW_ID,
    TIPWAVE_TENANT_ID,
)


def _get_tenant_or_raise(db_session: Session, tenant_id: UUID) -> Tenant:
    """Get tenant by ID or raise ValueError if not found.
    
    Args:
        db_session: Database session
        tenant_id: Tenant ID
    
    Returns:
        Tenant object
    
    Raises:
        ValueError: If tenant not found
    """
    tenant = db_session.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant is None:
        raise ValueError("tenant_not_found")
    return tenant


def get_cloud_account_for_scope(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> CloudAccount | None:
    """
    Resolve a cloud account for (tenant_id, cloud_account_id).

    The archived legacy Tipwave tenant has no rows of its own; it may use the shared
    Tipwave demo cloud account (same AWS account / same row id as current Tipwave).
    """
    tenant = db_session.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant is None:
        return None

    row = (
        db_session.query(CloudAccount)
        .filter(CloudAccount.tenant_id == tenant_id, CloudAccount.id == cloud_account_id)
        .first()
    )
    if row is not None:
        return row

    if (
        tenant.name == ARCHIVED_TIPWAVE_LEGACY_TENANT_NAME
        and cloud_account_id == TIPWAVE_CLOUD_ACCOUNT_ROW_ID
    ):
        return (
            db_session.query(CloudAccount)
            .filter(
                CloudAccount.tenant_id == TIPWAVE_TENANT_ID,
                CloudAccount.id == cloud_account_id,
            )
            .first()
        )
    return None


def get_cloud_account_or_raise(db_session: Session, tenant_id: UUID, cloud_account_id: UUID) -> CloudAccount:
    _get_tenant_or_raise(db_session, tenant_id)
    ca = get_cloud_account_for_scope(db_session, tenant_id, cloud_account_id)
    if ca is None:
        raise ValueError("cloud_account_not_found")
    return ca


def test_prospective_cloud_connection(
    account_id: str,
    role_arn: str,
    region_default: str,
) -> aws_validation_service.AwsValidationResult:
    """
    STS assume-role + GetCallerIdentity using credentials not yet stored (onboarding test).
    Callers must enforce org_admin / root_admin.
    """
    result = aws_validation_service.validate_cloud_account_role(
        role_arn=role_arn,
        region=region_default,
    )
    if not result.success:
        return result
    expected = normalize_aws_account_id(account_id)
    if result.aws_account_id and result.aws_account_id != expected:
        return aws_validation_service.AwsValidationResult(
            success=False,
            error_message=(
                f"Assume-role succeeded but caller identity account {result.aws_account_id} "
                f"does not match the provided account ID {expected}."
            ),
        )
    return result


def create_cloud_account(db_session: Session, tenant_id: UUID, data: CloudAccountCreate) -> CloudAccount:
    _get_tenant_or_raise(db_session, tenant_id)

    dup = (
        db_session.query(CloudAccount)
        .filter(CloudAccount.tenant_id == tenant_id, CloudAccount.account_id == data.account_id)
        .first()
    )
    if dup is not None:
        raise ValueError("cloud_account_duplicate")

    cloud_account = CloudAccount(
        tenant_id=tenant_id,
        account_id=data.account_id,
        name=data.name,
        status="pending",
        connection_status="untested",
        last_validated_at=None,
        last_validation_error=None,
        role_arn=data.role_arn,
        region_default=data.region_default,
    )
    db_session.add(cloud_account)
    try:
        db_session.commit()
    except IntegrityError:
        db_session.rollback()
        raise
    db_session.refresh(cloud_account)
    return cloud_account


def list_cloud_accounts(db_session: Session, tenant_id: UUID) -> list[CloudAccount]:
    tenant = _get_tenant_or_raise(db_session, tenant_id)

    rows = (
        db_session.query(CloudAccount)
        .filter(CloudAccount.tenant_id == tenant_id)
        .order_by(CloudAccount.created_at.asc())
        .all()
    )
    if rows:
        return rows
    if tenant.name == ARCHIVED_TIPWAVE_LEGACY_TENANT_NAME:
        shared = (
            db_session.query(CloudAccount)
            .filter(
                CloudAccount.tenant_id == TIPWAVE_TENANT_ID,
                CloudAccount.id == TIPWAVE_CLOUD_ACCOUNT_ROW_ID,
            )
            .first()
        )
        if shared is not None:
            return [shared]
    return rows


def get_cloud_account(db_session: Session, tenant_id: UUID, cloud_account_id: UUID) -> CloudAccount | None:
    _get_tenant_or_raise(db_session, tenant_id)
    return get_cloud_account_for_scope(db_session, tenant_id, cloud_account_id)


def validate_cloud_account(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> tuple[CloudAccount, aws_validation_service.AwsValidationResult]:
    """
    Validate a cloud account by assuming the role and verifying AWS identity.

    Args:
        db_session: Database session
        tenant_id: Tenant ID
        cloud_account_id: Cloud account ID

    Returns:
        Tuple of (updated CloudAccount, AwsValidationResult)

    Raises:
        ValueError: If tenant or cloud account not found
    """
    cloud_account = get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)

    # Call AWS validation service
    validation_result = aws_validation_service.validate_cloud_account_role(
        role_arn=cloud_account.role_arn,
        region=cloud_account.region_default,
    )
    if validation_result.success:
        expected = normalize_aws_account_id(cloud_account.account_id)
        if validation_result.aws_account_id and validation_result.aws_account_id != expected:
            validation_result = aws_validation_service.AwsValidationResult(
                success=False,
                error_message=(
                    f"Assume-role succeeded but caller identity account {validation_result.aws_account_id} "
                    f"does not match this cloud account's configured ID {expected}."
                ),
            )

    # Update cloud account status based on validation result
    now = utc_now()
    cloud_account.last_validated_at = now
    if validation_result.success:
        cloud_account.status = "connected"
        cloud_account.connection_status = "valid"
        cloud_account.last_validation_error = None
    else:
        cloud_account.status = "failed"
        cloud_account.connection_status = "invalid"
        cloud_account.last_validation_error = validation_result.error_message
    try:
        db_session.commit()
    except IntegrityError:
        db_session.rollback()
        raise
    db_session.refresh(cloud_account)

    return cloud_account, validation_result

