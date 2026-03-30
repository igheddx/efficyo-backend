"""AWS STS validation service for read-only cloud account verification."""

import logging
from dataclasses import dataclass
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.services import aws_assume_role_service

logger = logging.getLogger(__name__)


@dataclass
class AwsValidationResult:
    """Result of AWS validation attempt."""

    success: bool
    aws_account_id: Optional[str] = None
    arn: Optional[str] = None
    user_id: Optional[str] = None
    error_message: Optional[str] = None


def validate_cloud_account_role(
    role_arn: str,
    region: str = "us-east-1",
) -> AwsValidationResult:
    """
    Validate a cloud account by assuming a role and calling GetCallerIdentity.

    Args:
        role_arn: IAM role ARN to assume
        region: AWS region (default: us-east-1)

    Returns:
        AwsValidationResult with success status and caller identity info
    """
    try:
        # Assume the role
        credentials = aws_assume_role_service.assume_role(
            role_arn=role_arn,
            region=region,
            session_name="fptnext-validation",
        )

        # Create STS client with assumed credentials
        assumed_sts_client = boto3.client(
            "sts",
            region_name=region,
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
        )

        # Call GetCallerIdentity to verify the assumed role
        identity_response = assumed_sts_client.get_caller_identity()

        return AwsValidationResult(
            success=True,
            aws_account_id=identity_response.get("Account"),
            arn=identity_response.get("Arn"),
            user_id=identity_response.get("UserId"),
        )

    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        error_msg = exc.response["Error"]["Message"]
        logger.warning(f"AWS validation failed for role {role_arn}: {error_code} - {error_msg}")

        return AwsValidationResult(
            success=False,
            error_message=f"{error_code}: {error_msg}",
        )

    except BotoCoreError as exc:
        error_msg = str(exc)
        logger.warning(f"BotoCore error during AWS validation for role {role_arn}: {error_msg}")

        return AwsValidationResult(
            success=False,
            error_message=f"AWS connection failed: {error_msg}",
        )

    except Exception as exc:
        error_msg = str(exc)
        logger.error(f"Unexpected error during AWS validation for role {role_arn}: {error_msg}")

        return AwsValidationResult(
            success=False,
            error_message=f"Validation error: {error_msg}",
        )
