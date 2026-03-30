"""Minimal AWS role assumption helper service."""

import logging

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings
from app.core.demo_aws import is_tipwave_demo_role_arn

logger = logging.getLogger(__name__)


def _credentials_from_default_chain() -> dict:
    """Shape-compatible with STS AssumeRole Credentials for boto3 clients."""
    session = boto3.Session()
    creds = session.get_credentials()
    if creds is None:
        raise RuntimeError(
            "No AWS credentials in the default chain (AWS_PROFILE, ~/.aws/credentials, or env vars). "
            "Cannot use FPTNEXT_BYPASS_ASSUME_ROLE_FOR_TIPWAVE without credentials."
        )
    frozen = creds.get_frozen_credentials()
    return {
        "AccessKeyId": frozen.access_key,
        "SecretAccessKey": frozen.secret_key,
        "SessionToken": frozen.token or "",
    }


def assume_role(
    role_arn: str,
    region: str = "us-east-1",
    session_name: str = "fptnext-session",
    duration_seconds: int = 900,
) -> dict:
    """
    Assume an IAM role and return temporary credentials.

    Args:
        role_arn: IAM role ARN to assume
        region: AWS region
        session_name: Role session name
        duration_seconds: Credential duration (default: 900 = 15 minutes)

    Returns:
        Credentials dict with AccessKeyId, SecretAccessKey, SessionToken

    Raises:
        ClientError: On AWS API errors
        BotoCoreError: On AWS SDK errors
    """
    if settings.bypass_assume_role_for_tipwave_demo and is_tipwave_demo_role_arn(role_arn):
        logger.info(
            "FPTNEXT_BYPASS_ASSUME_ROLE_FOR_TIPWAVE: using default AWS credential chain instead of sts:AssumeRole "
            "for Tipwave demo role %s",
            role_arn,
        )
        return _credentials_from_default_chain()

    try:
        sts_client = boto3.client("sts", region_name=region)
        response = sts_client.assume_role(
            RoleArn=role_arn,
            RoleSessionName=session_name,
            DurationSeconds=duration_seconds,
        )
        return response["Credentials"]
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        error_msg = exc.response["Error"]["Message"]
        msg = f"AWS error assuming role {role_arn}: {error_code} - {error_msg}"
        if is_tipwave_demo_role_arn(role_arn):
            logger.warning("%s (Tipwave demo role; check trust policy for your IAM user)", msg)
        else:
            logger.error(msg)
        raise
    except BotoCoreError as exc:
        msg = f"BotoCore error assuming role {role_arn}: {str(exc)}"
        if is_tipwave_demo_role_arn(role_arn):
            logger.warning("%s (Tipwave demo role)", msg)
        else:
            logger.error(msg)
        raise
