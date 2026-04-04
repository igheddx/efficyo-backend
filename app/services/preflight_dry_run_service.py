"""Preflight checks and dry-run previews for allowlisted S3 recommendations."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy.orm import Session

from app.models.recommendation import Recommendation
from app.services.execution_constants import SAFE_AUTO_EXECUTION_TYPES
from app.services.safe_execution_service import (
    _get_cloud_account_or_raise,
    _get_recommendation_or_raise,
    _s3_client_for_cloud,
)

_ALLOWLIST = SAFE_AUTO_EXECUTION_TYPES

_DEFAULT_PAB = {
    "BlockPublicAcls": False,
    "IgnorePublicAcls": False,
    "BlockPublicPolicy": False,
    "RestrictPublicBuckets": False,
}

_TARGET_PAB = {
    "BlockPublicAcls": True,
    "IgnorePublicAcls": True,
    "BlockPublicPolicy": True,
    "RestrictPublicBuckets": True,
}


def _aggregate_preflight_status(checks: list[dict[str, Any]]) -> str:
    has_fail = any(c["status"] == "fail" for c in checks)
    has_warn = any(c["status"] == "warning" for c in checks)
    if has_fail:
        return "blocked"
    if has_warn:
        return "warning"
    return "ready"


def _get_bucket_tags(s3_client, bucket: str) -> dict[str, str]:
    try:
        response = s3_client.get_bucket_tagging(Bucket=bucket)
        return {str(t["Key"]): str(t.get("Value") or "") for t in response.get("TagSet", []) or []}
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in {"NoSuchTagSet", "NoSuchTagSetError"}:
            return {}
        raise


def _merge_tags_for_tags_recommendation(
    existing: dict[str, str],
    tag_values: Optional[dict[str, str]],
) -> dict[str, str]:
    merged = dict(existing)
    provided = tag_values or {}
    for k, v in provided.items():
        merged[str(k)] = str(v)
    if "Name" not in merged:
        merged["Name"] = provided.get("Name", "<set-name>")
    if "Environment" not in merged:
        merged["Environment"] = provided.get("Environment", "<set-environment>")
    return merged


def _preflight_aws_client_error_result(rec: Recommendation, exc: ClientError) -> dict[str, Any]:
    code = str(exc.response.get("Error", {}).get("Code", "") or "")
    msg = str(exc.response.get("Error", {}).get("Message", "") or "")
    checks = [
        {
            "name": "aws_access",
            "status": "fail",
            "message": (
                f"Could not access AWS for preflight ({code or 'ClientError'}): {msg}. "
                "Check the cloud account role trust policy and sts:AssumeRole permissions."
            ),
        }
    ]
    return {
        "recommendation_id": rec.id,
        "status": "blocked",
        "risk_level": rec.risk_level,
        "safe_to_apply": False,
        "checks": checks,
    }


def _preflight_boto_error_result(rec: Recommendation, exc: BotoCoreError) -> dict[str, Any]:
    checks = [
        {
            "name": "aws_access",
            "status": "fail",
            "message": f"Could not access AWS for preflight: {exc}",
        }
    ]
    return {
        "recommendation_id": rec.id,
        "status": "blocked",
        "risk_level": rec.risk_level,
        "safe_to_apply": False,
        "checks": checks,
    }


def run_preflight(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
) -> dict[str, Any]:
    rec = _get_recommendation_or_raise(db_session, tenant_id, cloud_account_id, recommendation_id)
    rtype = (rec.recommendation_type or "").lower()
    checks: list[dict[str, Any]] = []

    if rtype not in _ALLOWLIST:
        checks.append(
            {
                "name": "supported_recommendation_type",
                "status": "fail",
                "message": f"Preflight is only implemented for: {', '.join(sorted(_ALLOWLIST))}",
            }
        )
        return {
            "recommendation_id": rec.id,
            "status": "blocked",
            "risk_level": rec.risk_level,
            "safe_to_apply": False,
            "checks": checks,
        }

    cloud = _get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)
    try:
        s3 = _s3_client_for_cloud(cloud)
    except ClientError as exc:
        return _preflight_aws_client_error_result(rec, exc)
    except BotoCoreError as exc:
        return _preflight_boto_error_result(rec, exc)
    bucket = rec.resource_id

    # --- bucket exists ---
    try:
        s3.head_bucket(Bucket=bucket)
        checks.append({"name": "bucket_exists", "status": "pass", "message": "Bucket is reachable"})
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"404", "NoSuchBucket", "NotFound"}:
            msg = "Bucket does not exist or is not visible in this account/region"
        elif code in {"403", "AccessDenied"}:
            msg = "Access denied listing bucket (check IAM for s3:HeadBucket / s3:ListBucket)"
        else:
            msg = f"Could not verify bucket: {code}"
        checks.append({"name": "bucket_exists", "status": "fail", "message": msg})
        return {
            "recommendation_id": rec.id,
            "status": "blocked",
            "risk_level": rec.risk_level,
            "safe_to_apply": False,
            "checks": checks,
        }
    except BotoCoreError as exc:
        checks.append({"name": "bucket_exists", "status": "fail", "message": str(exc)})
        return {
            "recommendation_id": rec.id,
            "status": "blocked",
            "risk_level": rec.risk_level,
            "safe_to_apply": False,
            "checks": checks,
        }

    if rtype == "s3_enable_public_access_block":
        pab: dict[str, bool] = dict(_DEFAULT_PAB)
        try:
            resp = s3.get_public_access_block(Bucket=bucket)
            cfg = resp.get("PublicAccessBlockConfiguration") or {}
            for k in _DEFAULT_PAB:
                pab[k] = bool(cfg.get(k))
            checks.append({"name": "read_public_access_block", "status": "pass", "message": "Fetched current public access block configuration"})
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "NoSuchPublicAccessBlockConfiguration":
                checks.append(
                    {
                        "name": "read_public_access_block",
                        "status": "pass",
                        "message": "No existing block configuration (defaults apply; remediation needed)",
                    }
                )
            elif code in {"403", "AccessDenied"}:
                checks.append(
                    {
                        "name": "read_public_access_block",
                        "status": "fail",
                        "message": "Cannot read public access block (s3:GetPublicAccessBlock)",
                    }
                )
                return {
                    "recommendation_id": rec.id,
                    "status": "blocked",
                    "risk_level": rec.risk_level,
                    "safe_to_apply": False,
                    "checks": checks,
                }
            else:
                checks.append(
                    {
                        "name": "read_public_access_block",
                        "status": "fail",
                        "message": f"Unexpected error reading configuration: {code}",
                    }
                )
                return {
                    "recommendation_id": rec.id,
                    "status": "blocked",
                    "risk_level": rec.risk_level,
                    "safe_to_apply": False,
                    "checks": checks,
                }

        if pab == _TARGET_PAB:
            checks.append(
                {
                    "name": "change_still_required",
                    "status": "warning",
                    "message": "All public access block settings are already enabled; apply would be idempotent",
                }
            )
        else:
            checks.append(
                {
                    "name": "change_still_required",
                    "status": "pass",
                    "message": "At least one public access block setting is not fully enabled",
                }
            )

        checks.append(
            {
                "name": "aws_write_permissions",
                "status": "warning",
                "message": "Write permission for s3:PutPublicAccessBlock not verified without applying (best effort)",
            }
        )

    elif rtype == "s3_add_required_tags":
        try:
            existing = _get_bucket_tags(s3, bucket)
            checks.append({"name": "read_bucket_tags", "status": "pass", "message": "Fetched current tag set"})
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"403", "AccessDenied"}:
                checks.append(
                    {
                        "name": "read_bucket_tags",
                        "status": "fail",
                        "message": "Cannot read bucket tags (s3:GetBucketTagging)",
                    }
                )
                return {
                    "recommendation_id": rec.id,
                    "status": "blocked",
                    "risk_level": rec.risk_level,
                    "safe_to_apply": False,
                    "checks": checks,
                }
            checks.append({"name": "read_bucket_tags", "status": "fail", "message": f"Could not read tags: {code}"})
            return {
                "recommendation_id": rec.id,
                "status": "blocked",
                "risk_level": rec.risk_level,
                "safe_to_apply": False,
                "checks": checks,
            }

        merged = _merge_tags_for_tags_recommendation(existing, None)
        has_placeholders = merged.get("Name") == "<set-name>" or merged.get("Environment") == "<set-environment>"
        if existing.get("Name") and existing.get("Environment") and not has_placeholders:
            checks.append(
                {
                    "name": "change_still_required",
                    "status": "warning",
                    "message": "Name and Environment tags already present; verify values before overwriting via Run Fix",
                }
            )
        elif has_placeholders:
            checks.append(
                {
                    "name": "change_still_required",
                    "status": "warning",
                    "message": "Name and/or Environment still need values (provide in Run Fix or execution plan)",
                }
            )
        else:
            checks.append(
                {
                    "name": "change_still_required",
                    "status": "pass",
                    "message": "Tag merge will add or update required keys",
                }
            )

        checks.append(
            {
                "name": "aws_write_permissions",
                "status": "warning",
                "message": "Write permission for s3:PutBucketTagging not verified without applying (best effort)",
            }
        )

    status = _aggregate_preflight_status(checks)
    safe = status != "blocked"
    return {
        "recommendation_id": rec.id,
        "status": status,
        "risk_level": rec.risk_level,
        "safe_to_apply": safe,
        "checks": checks,
    }


def _dry_run_aws_client_error_result(rec: Recommendation, exc: ClientError) -> dict[str, Any]:
    code = str(exc.response.get("Error", {}).get("Code", "") or "")
    msg = str(exc.response.get("Error", {}).get("Message", "") or "")
    rtype = (rec.recommendation_type or "").lower()
    return {
        "recommendation_id": rec.id,
        "recommendation_type": rtype,
        "risk_level": rec.risk_level,
        "before": {},
        "after": {},
        "impact_summary": (
            f"Could not load AWS state for dry-run ({code or 'ClientError'}): {msg}. "
            "Check the execution role can read bucket configuration (e.g. s3:GetBucketTagging, s3:GetPublicAccessBlock)."
        ),
    }


def run_dry_run(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
    tag_values: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    rec = _get_recommendation_or_raise(db_session, tenant_id, cloud_account_id, recommendation_id)
    rtype = (rec.recommendation_type or "").lower()

    if rtype not in _ALLOWLIST:
        return {
            "recommendation_id": rec.id,
            "recommendation_type": rtype,
            "risk_level": rec.risk_level,
            "before": {},
            "after": {},
            "impact_summary": "Dry-run is only available for S3 public access block and required-tags recommendations.",
        }

    cloud = _get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)
    s3 = _s3_client_for_cloud(cloud)
    bucket = rec.resource_id

    try:
        if rtype == "s3_enable_public_access_block":
            before_cfg: dict[str, Any] = dict(_DEFAULT_PAB)
            try:
                resp = s3.get_public_access_block(Bucket=bucket)
                cfg = resp.get("PublicAccessBlockConfiguration") or {}
                for k in _DEFAULT_PAB:
                    before_cfg[k] = bool(cfg.get(k))
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code != "NoSuchPublicAccessBlockConfiguration":
                    raise
            after_cfg = dict(_TARGET_PAB)
            summary = (
                "Will set BlockPublicAcls, IgnorePublicAcls, BlockPublicPolicy, and RestrictPublicBuckets to true "
                f"for bucket {bucket}."
            )
            return {
                "recommendation_id": rec.id,
                "recommendation_type": rtype,
                "risk_level": rec.risk_level,
                "before": {"bucket": bucket, "public_access_block_configuration": before_cfg},
                "after": {"bucket": bucket, "public_access_block_configuration": after_cfg},
                "impact_summary": summary,
            }

        # s3_add_required_tags
        existing = _get_bucket_tags(s3, bucket)
        merged = _merge_tags_for_tags_recommendation(existing, tag_values)
        before_sorted = dict(sorted(existing.items(), key=lambda x: x[0]))
        after_sorted = dict(sorted(merged.items(), key=lambda x: x[0]))
        summary = (
            f"Will apply tag set with {len(after_sorted)} keys on bucket {bucket}, "
            "preserving existing tags and ensuring Name and Environment are present."
        )
        return {
            "recommendation_id": rec.id,
            "recommendation_type": rtype,
            "risk_level": rec.risk_level,
            "before": {"bucket": bucket, "tags": before_sorted},
            "after": {"bucket": bucket, "tags": after_sorted},
            "impact_summary": summary,
        }
    except ClientError as exc:
        return _dry_run_aws_client_error_result(rec, exc)
