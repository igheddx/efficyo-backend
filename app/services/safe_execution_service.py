"""Safe auto-apply pilot for explicitly allowlisted recommendation types."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Optional
from uuid import UUID

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy.orm import Session

from app.models.cloud_account import CloudAccount
from app.models.recommendation import Recommendation
from app.models.tenant import Tenant
from app.services import approval_request_service, aws_assume_role_service, recommendation_outcome_service
from app.services.cloud_account_service import get_cloud_account_or_raise as _get_cloud_account_or_raise
from app.services.execution_audit_service import log_execution_audit_event
from app.services.execution_constants import is_safe_auto_execution_type
from app.services.execution_policy_service import resolve_execution_policy

logger = logging.getLogger(__name__)


def _get_recommendation_or_raise(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
) -> Recommendation:
    rec = (
        db_session.query(Recommendation)
        .filter(
            Recommendation.tenant_id == tenant_id,
            Recommendation.cloud_account_id == cloud_account_id,
            Recommendation.id == recommendation_id,
        )
        .first()
    )
    if rec is None:
        raise ValueError("recommendation_not_found")
    return rec


def _s3_client_for_cloud(cloud: CloudAccount):
    credentials = aws_assume_role_service.assume_role(
        role_arn=cloud.role_arn,
        region=cloud.region_default or "us-east-1",
        session_name="fptnext-safe-execution",
    )
    return boto3.client(
        "s3",
        region_name=cloud.region_default or "us-east-1",
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )


def _execute_s3_public_access_block(
    s3_client,
    bucket_name: str,
) -> tuple[str, str]:
    response = s3_client.put_public_access_block(
        Bucket=bucket_name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    req_id = response.get("ResponseMetadata", {}).get("RequestId")
    notes = f"Applied S3 Public Access Block on bucket {bucket_name}. RequestId={req_id or 'n/a'}."
    rollback = (
        "To rollback, call put_public_access_block for the same bucket and set any of "
        "BlockPublicAcls/IgnorePublicAcls/BlockPublicPolicy/RestrictPublicBuckets to false."
    )
    return notes, rollback


def _execute_s3_add_required_tags(
    s3_client,
    bucket_name: str,
    tag_values: Optional[dict[str, str]],
) -> tuple[str, str]:
    existing: dict[str, str] = {}
    try:
        response = s3_client.get_bucket_tagging(Bucket=bucket_name)
        for t in response.get("TagSet", []) or []:
            k = t.get("Key")
            v = t.get("Value")
            if k:
                existing[str(k)] = str(v or "")
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code not in {"NoSuchTagSet", "NoSuchTagSetError"}:
            raise

    merged = dict(existing)
    provided = tag_values or {}
    for k, v in provided.items():
        merged[str(k)] = str(v)

    if "Name" not in merged:
        merged["Name"] = provided.get("Name", "<set-name>")
    if "Environment" not in merged:
        merged["Environment"] = provided.get("Environment", "<set-environment>")

    tag_set = [{"Key": k, "Value": v} for k, v in sorted(merged.items(), key=lambda x: x[0])]
    put_resp = s3_client.put_bucket_tagging(Bucket=bucket_name, Tagging={"TagSet": tag_set})
    req_id = put_resp.get("ResponseMetadata", {}).get("RequestId")
    notes = (
        f"Updated bucket tags on {bucket_name}. Added/kept {len(tag_set)} tags including Name/Environment. "
        f"RequestId={req_id or 'n/a'}."
    )
    rollback = "To rollback, remove or modify tags with put_bucket_tagging for the same bucket."
    return notes, rollback


def execute_recommendation(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
    executed_by: str,
    executed_role: str | None = None,
    tag_values: Optional[dict[str, str]] = None,
    *,
    applied_membership_role: str | None = None,
    applied_access_role: str | None = None,
) -> dict:
    cloud = _get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)
    rec = _get_recommendation_or_raise(db_session, tenant_id, cloud_account_id, recommendation_id)

    outcome = recommendation_outcome_service.create_outcome_for_recommendation(
        db_session=db_session,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        recommendation_id=recommendation_id,
    )

    workflow_status = recommendation_outcome_service.normalized_workflow_status(outcome.workflow_status)
    if workflow_status == "rejected":
        raise ValueError("workflow_rejected")
    if workflow_status != "approved":
        raise ValueError("workflow_not_approved")

    blocked, gate_code = approval_request_service.execution_blocked_by_approval_request(
        db_session, tenant_id, cloud_account_id, recommendation_id
    )
    if blocked:
        raise ValueError(gate_code or "approval_request_pending")

    tenant_row = db_session.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant_row is None or tenant_row.organization_id is None:
        raise ValueError("tenant_not_found")

    rtype = (rec.recommendation_type or "").lower()
    if not is_safe_auto_execution_type(rtype):
        log_execution_audit_event(
            db_session,
            event_type="execution_blocked",
            organization_id=tenant_row.organization_id,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            recommendation_id=recommendation_id,
            actor_email=executed_by,
            execution_trigger="manual_api",
            allowed=False,
            blocking_reason="unsupported_recommendation_type",
            detail_json={"recommendation_type": rtype},
        )
        raise ValueError("unsupported_recommendation_type")
    policy = resolve_execution_policy(
        db_session,
        organization_id=tenant_row.organization_id,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        recommendation_type=rec.recommendation_type,
        recommendation_risk_level=rec.risk_level,
    )
    db_session.refresh(outcome)
    if policy.preflight_required and outcome.preflight_passed_at is None:
        log_execution_audit_event(
            db_session,
            event_type="execution_blocked",
            organization_id=tenant_row.organization_id,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            recommendation_id=recommendation_id,
            execution_policy_id=policy.policy_row_id,
            actor_email=executed_by,
            execution_trigger="manual_api",
            allowed=False,
            blocking_reason="preflight_required_not_passed",
            detail_json={
                "policy_scope_level": policy.scope_level,
                "effective_execution_mode": policy.execution_mode,
            },
        )
        raise ValueError("preflight_required_not_passed")

    try:
        from app.services import notification_service

        summary = notification_service.recommendation_summary(
            db_session, tenant_id, cloud_account_id, recommendation_id
        )
        notification_service.notify_execution_started(
            db_session,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            recommendation_id=recommendation_id,
            summary=summary,
        )
    except Exception:
        logger.debug("execution_started notification skipped", exc_info=True)

    db_session.refresh(outcome)
    if recommendation_outcome_service.set_proof_before_cost_if_missing(
        db_session, outcome, tenant_id, cloud_account_id
    ):
        db_session.add(outcome)
        db_session.commit()
        db_session.refresh(outcome)

    s3_client = _s3_client_for_cloud(cloud)
    notes = ""
    rollback = "Capture current configuration first and revert to known-good state if needed."

    try:
        if rtype == "s3_enable_public_access_block":
            notes, rollback = _execute_s3_public_access_block(s3_client, rec.resource_id)
        elif rtype == "s3_add_required_tags":
            notes, rollback = _execute_s3_add_required_tags(s3_client, rec.resource_id, tag_values)
    except (ClientError, BotoCoreError) as exc:
        fail_note = f"Execution failed for {rec.recommendation_type} on {rec.resource_id}: {str(exc)}"
        log_execution_audit_event(
            db_session,
            event_type="execution_failed",
            organization_id=tenant_row.organization_id,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            recommendation_id=recommendation_id,
            execution_policy_id=policy.policy_row_id,
            actor_email=executed_by,
            execution_trigger="manual_api",
            allowed=False,
            blocking_reason="aws_client_error",
            detail_json={"message": str(exc)[:500]},
        )
        outcome.execution_notes = fail_note
        db_session.add(outcome)
        db_session.commit()
        logger.exception("Safe execution failed", extra={"recommendation_id": str(recommendation_id)})
        try:
            from app.services import notification_service

            summary = notification_service.recommendation_summary(
                db_session, tenant_id, cloud_account_id, recommendation_id
            )
            notification_service.notify_execution_failed(
                db_session,
                tenant_id=tenant_id,
                cloud_account_id=cloud_account_id,
                recommendation_id=recommendation_id,
                summary=summary,
                detail=str(exc),
            )
        except Exception:
            logger.debug("execution_failed notification skipped", exc_info=True)
        raise

    updated = recommendation_outcome_service.mark_recommendation_applied(
        db_session=db_session,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        recommendation_id=recommendation_id,
        applied_by=executed_by,
        applied_role=executed_role,
        execution_notes=notes,
        applied_membership_role=applied_membership_role,
        applied_access_role=applied_access_role,
        applied_via_auto=False,
    )

    log_execution_audit_event(
        db_session,
        event_type="execution_manual_completed",
        organization_id=tenant_row.organization_id,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        recommendation_id=recommendation_id,
        execution_policy_id=policy.policy_row_id,
        actor_email=executed_by,
        execution_trigger="manual_api",
        allowed=True,
        blocking_reason=None,
        detail_json={
            "policy_scope_level": policy.scope_level,
            "effective_execution_mode": policy.execution_mode,
            "recommendation_type": rtype,
        },
    )

    return {
        "recommendation_id": rec.id,
        "execution_status": "success",
        "workflow_status": recommendation_outcome_service.normalized_workflow_status(updated.workflow_status),
        "applied_by": updated.applied_by,
        "applied_role": updated.applied_role,
        "applied_at": updated.applied_at,
        "execution_notes": updated.execution_notes,
        "rollback_guidance": rollback,
    }

