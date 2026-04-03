from __future__ import annotations

from collections import Counter
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.db import utc_now
from app.models.approval_request import ApprovalRequest
from app.models.execution_owner import ExecutionOwnerAssignment
from app.models.finding import Finding
from app.models.recommendation import Recommendation
from app.models.recommendation_outcome import RecommendationOutcome
from app.models.tagging_batch import TaggingBatch, TaggingBatchResource
from app.services import approval_request_service, recommendation_outcome_service
from app.services.recommendation_service import guided_actions_for_type
from app.services.resource_capability_registry import TAG_GOVERNANCE_FINDING_TO_RECOMMENDATION


def grouped_recommendations(
    db_session: Session,
    *,
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_reads: list,
) -> list[dict]:
    owner_rows = (
        db_session.query(ExecutionOwnerAssignment)
        .filter(
            ExecutionOwnerAssignment.tenant_id == tenant_id,
            ExecutionOwnerAssignment.cloud_account_id == cloud_account_id,
        )
        .all()
    )
    owner_by_recommendation_id = {str(r.recommendation_id): r.owner_user_id for r in owner_rows}

    grouped: dict[str, dict] = {}
    for rec in recommendation_reads:
        key = rec.recommendation_type
        if key not in grouped:
            steps, _eta, _difficulty = guided_actions_for_type(key)
            grouped[key] = {
                "group_key": key,
                "total_count": 0,
                "resource_type_breakdown": Counter(),
                "risk_summary": Counter(),
                "workflow_summary": Counter(),
                "owner_summary": Counter(),
                "guided_actions": steps,
                "resources": [],
            }
        bucket = grouped[key]
        bucket["total_count"] += 1
        bucket["resource_type_breakdown"][rec.resource_type] += 1
        bucket["risk_summary"][rec.risk_level or "unknown"] += 1
        bucket["workflow_summary"][rec.workflow_status or "suggested"] += 1
        owner_id = owner_by_recommendation_id.get(str(rec.id))
        if owner_id is not None:
            bucket["owner_summary"][str(owner_id)] += 1

        evidence = rec.evidence_json if isinstance(rec.evidence_json, dict) else {}
        bucket["resources"].append(
            {
                "recommendation_id": rec.id,
                "resource_id": rec.resource_id,
                "resource_type": rec.resource_type,
                "summary": rec.summary,
                "risk_level": rec.risk_level,
                "workflow_status": rec.workflow_status,
                "current_tags": evidence.get("tags") if isinstance(evidence.get("tags"), dict) else {},
                "missing_required_tags": list(evidence.get("missing_tags") or []),
            }
        )

    out = []
    for row in grouped.values():
        out.append(
            {
                "group_key": row["group_key"],
                "total_count": row["total_count"],
                "resource_type_breakdown": dict(row["resource_type_breakdown"]),
                "risk_summary": dict(row["risk_summary"]),
                "workflow_summary": dict(row["workflow_summary"]),
                "owner_summary": {
                    "assigned": int(sum(row["owner_summary"].values())),
                    "unassigned": int(row["total_count"] - sum(row["owner_summary"].values())),
                },
                "guided_actions": row["guided_actions"],
                "resources": row["resources"],
            }
        )
    out.sort(key=lambda x: (x["total_count"], x["group_key"]), reverse=True)
    return out


def grouped_findings(
    *,
    findings: list[Finding],
) -> list[dict]:
    grouped: dict[str, dict] = {}
    for finding in findings:
        key = finding.finding_type
        if key not in grouped:
            related = TAG_GOVERNANCE_FINDING_TO_RECOMMENDATION.get(key)
            grouped[key] = {
                "group_key": key,
                "total_count": 0,
                "resource_type_breakdown": Counter(),
                "severity_summary": Counter(),
                "related_recommendation_type": related[0] if related else None,
                "resources": [],
            }
        bucket = grouped[key]
        bucket["total_count"] += 1
        bucket["resource_type_breakdown"][finding.resource_type] += 1
        bucket["severity_summary"][finding.severity] += 1

        ev = finding.evidence_json if isinstance(finding.evidence_json, dict) else {}
        bucket["resources"].append(
            {
                "finding_id": finding.id,
                "resource_id": finding.resource_id,
                "resource_type": finding.resource_type,
                "severity": finding.severity,
                "summary": f"{finding.finding_type} on {finding.resource_id}",
                "current_tags": ev.get("tags") if isinstance(ev.get("tags"), dict) else {},
                "missing_required_tags": list(ev.get("missing_tags") or []),
            }
        )

    out = []
    for row in grouped.values():
        out.append(
            {
                "group_key": row["group_key"],
                "total_count": row["total_count"],
                "resource_type_breakdown": dict(row["resource_type_breakdown"]),
                "severity_summary": dict(row["severity_summary"]),
                "related_recommendation_type": row["related_recommendation_type"],
                "resources": row["resources"],
            }
        )
    out.sort(key=lambda x: (x["total_count"], x["group_key"]), reverse=True)
    return out


def create_tagging_batch(
    db_session: Session,
    *,
    organization_id: UUID,
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_type: str,
    title: str | None,
    required_tag_keys: list[str],
    shared_tag_values: dict[str, str],
    resources: list[dict],
    approver_user_ids: list[UUID],
    execution_owner_user_id: UUID,
    submitted_by: str | None,
    submitted_by_role: str | None,
    notes: str | None,
) -> TaggingBatch:
    rec_ids = [r["recommendation_id"] for r in resources]
    rec_rows = (
        db_session.query(Recommendation)
        .filter(
            Recommendation.tenant_id == tenant_id,
            Recommendation.cloud_account_id == cloud_account_id,
            Recommendation.id.in_(rec_ids),
        )
        .all()
    )
    rec_by_id = {str(r.id): r for r in rec_rows}
    if len(rec_by_id) != len(set(str(x) for x in rec_ids)):
        raise ValueError("recommendation_not_found")

    for rec in rec_rows:
        if rec.recommendation_type != recommendation_type:
            raise ValueError("recommendation_type_mismatch")

    representative_recommendation_id = resources[0]["recommendation_id"]
    approval_request = approval_request_service.create_approval_request(
        db_session,
        organization_id=organization_id,
        cloud_account_id=cloud_account_id,
        recommendation_id=representative_recommendation_id,
        approver_user_ids=approver_user_ids,
        execution_owner_user_id=execution_owner_user_id,
        approval_mode="all_required",
        tag_values=shared_tag_values,
        submitted_by=submitted_by,
        submitted_by_role=submitted_by_role,
    )

    resource_type_counts = Counter()
    tag_key_counts = Counter()
    for row in resources:
        rec = rec_by_id[str(row["recommendation_id"])]
        resource_type_counts[rec.resource_type] += 1
        for k in (row.get("proposed_tags") or {}).keys():
            tag_key_counts[k] += 1

    batch = TaggingBatch(
        organization_id=organization_id,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        approval_request_id=approval_request.id,
        recommendation_type=recommendation_type,
        title=title or f"Bulk Tagging Review: {recommendation_type}",
        status="pending",
        requested_by=submitted_by,
        requested_by_role=submitted_by_role,
        execution_notes=notes,
        required_tag_keys_json=required_tag_keys or None,
        shared_tags_json=shared_tag_values or None,
        summary_json={
            "resource_count": len(resources),
            "resource_type_breakdown": dict(resource_type_counts),
            "tag_keys": sorted(tag_key_counts.keys()),
            "tag_key_coverage": dict(tag_key_counts),
        },
    )
    db_session.add(batch)
    db_session.flush()

    for row in resources:
        rec = rec_by_id[str(row["recommendation_id"])]
        outcome = (
            db_session.query(RecommendationOutcome)
            .filter(
                RecommendationOutcome.tenant_id == tenant_id,
                RecommendationOutcome.cloud_account_id == cloud_account_id,
                RecommendationOutcome.recommendation_id == rec.id,
            )
            .first()
        )
        evidence = (
            db_session.query(Finding.evidence_json)
            .filter(Finding.id == rec.finding_id)
            .first()
        )
        ev = evidence[0] if evidence and isinstance(evidence[0], dict) else {}

        db_session.add(
            TaggingBatchResource(
                batch_id=batch.id,
                recommendation_id=rec.id,
                resource_id=rec.resource_id,
                resource_type=rec.resource_type,
                current_tags_json=(outcome.tag_values_json if outcome and outcome.tag_values_json else ev.get("tags") or {}),
                missing_required_tags_json=list(ev.get("missing_tags") or []),
                proposed_tags_json=row.get("proposed_tags") or {},
                context_json={
                    "tenant_id": str(tenant_id),
                    "cloud_account_id": str(cloud_account_id),
                },
                execution_status="pending_approval",
            )
        )

    db_session.commit()
    db_session.refresh(batch)
    _ = batch.resources
    return batch


def list_batches(
    db_session: Session,
    *,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> list[TaggingBatch]:
    rows = (
        db_session.query(TaggingBatch)
        .filter(
            TaggingBatch.tenant_id == tenant_id,
            TaggingBatch.cloud_account_id == cloud_account_id,
        )
        .order_by(TaggingBatch.created_at.desc())
        .all()
    )
    for row in rows:
        _ = row.resources
    return rows


def get_batch(
    db_session: Session,
    *,
    tenant_id: UUID,
    cloud_account_id: UUID,
    batch_id: UUID,
) -> TaggingBatch | None:
    row = (
        db_session.query(TaggingBatch)
        .filter(
            TaggingBatch.id == batch_id,
            TaggingBatch.tenant_id == tenant_id,
            TaggingBatch.cloud_account_id == cloud_account_id,
        )
        .first()
    )
    if row is None:
        return None
    _ = row.resources
    return row


def on_approval_request_approved(
    db_session: Session,
    *,
    approval_request_id: UUID,
    approved_by: str | None,
    approved_role: str | None,
    approved_membership_role: str | None,
    approved_access_role: str | None,
) -> None:
    batch = (
        db_session.query(TaggingBatch)
        .filter(TaggingBatch.approval_request_id == approval_request_id)
        .first()
    )
    if batch is None:
        return

    batch.status = "approved"
    batch.updated_at = utc_now()
    for row in batch.resources:
        row.execution_status = "approved"
        recommendation_outcome_service.approve_recommendation(
            db_session=db_session,
            tenant_id=batch.tenant_id,
            cloud_account_id=batch.cloud_account_id,
            recommendation_id=row.recommendation_id,
            approved_by=approved_by,
            approved_role=approved_role,
            approval_comment=f"Batch approval {batch.id} approved.",
            approved_membership_role=approved_membership_role,
            approved_access_role=approved_access_role,
        )


def on_approval_request_rejected(db_session: Session, *, approval_request_id: UUID, reason: str | None) -> None:
    batch = (
        db_session.query(TaggingBatch)
        .filter(TaggingBatch.approval_request_id == approval_request_id)
        .first()
    )
    if batch is None:
        return
    batch.status = "rejected"
    batch.updated_at = utc_now()
    for row in batch.resources:
        row.execution_status = "blocked"
        if reason:
            row.execution_error = reason


def execute_batch(
    db_session: Session,
    *,
    tenant_id: UUID,
    cloud_account_id: UUID,
    batch_id: UUID,
    actor_email: str | None,
    actor_role: str | None,
    execution_notes: str | None,
) -> dict:
    batch = get_batch(db_session, tenant_id=tenant_id, cloud_account_id=cloud_account_id, batch_id=batch_id)
    if batch is None:
        raise ValueError("batch_not_found")

    if batch.approval_request_id is None:
        raise ValueError("batch_without_approval_request")

    req = db_session.query(ApprovalRequest).filter(ApprovalRequest.id == batch.approval_request_id).first()
    if req is None:
        raise ValueError("approval_request_not_found")
    if req.status != "approved":
        raise ValueError("batch_not_approved")

    batch.status = "in_progress"
    batch.execution_notes = execution_notes if execution_notes is not None else batch.execution_notes

    for row in batch.resources:
        if row.execution_status in {"completed"}:
            continue
        row.execution_status = "in_progress"
        try:
            outcome = recommendation_outcome_service.create_outcome_for_recommendation(
                db_session=db_session,
                tenant_id=tenant_id,
                cloud_account_id=cloud_account_id,
                recommendation_id=row.recommendation_id,
            )
            outcome.tag_values_json = dict(row.proposed_tags_json or {})
            db_session.add(outcome)

            recommendation_outcome_service.mark_recommendation_applied(
                db_session=db_session,
                tenant_id=tenant_id,
                cloud_account_id=cloud_account_id,
                recommendation_id=row.recommendation_id,
                applied_by=actor_email,
                applied_role=actor_role,
                execution_notes=execution_notes,
            )
            row.execution_status = "completed"
            row.executed_at = utc_now()
            row.execution_error = None
        except Exception as exc:  # noqa: BLE001
            row.execution_status = "failed"
            row.execution_error = str(exc)

    counts = Counter([r.execution_status for r in batch.resources])
    failed = counts.get("failed", 0)
    in_progress = counts.get("in_progress", 0)
    if failed > 0:
        batch.status = "failed"
    elif in_progress > 0:
        batch.status = "in_progress"
    else:
        batch.status = "completed"

    db_session.commit()
    db_session.refresh(batch)

    return {
        "batch_id": batch.id,
        "status": batch.status,
        "pending": counts.get("pending", 0) + counts.get("pending_approval", 0),
        "approved": counts.get("approved", 0),
        "in_progress": counts.get("in_progress", 0),
        "completed": counts.get("completed", 0),
        "failed": counts.get("failed", 0),
        "blocked": counts.get("blocked", 0),
    }
