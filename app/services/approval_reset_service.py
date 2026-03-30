"""Clear approval requests and related notifications (local/dev reset)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.approval_request import ApprovalAssignment, ApprovalRequest
from app.models.notification import Notification
from app.models.recommendation_outcome import RecommendationOutcome
from app.models.tenant import Tenant


APPROVAL_NOTIFICATION_TYPES = frozenset({"approval_required", "approval_completed"})


def clear_approval_data(
    db_session: Session,
    *,
    tenant_id: UUID | None = None,
    cloud_account_id: UUID | None = None,
) -> dict[str, int]:
    """
    Delete approval assignments + requests, and in-app notifications tied to the approval UX.

    If ``tenant_id`` / ``cloud_account_id`` are set, only approval_requests matching both are removed
    (assignments cascade via FK). Otherwise all rows in those tables are cleared.
    """
    counts: dict[str, int] = {}

    scoped = tenant_id is not None or cloud_account_id is not None
    q_req = db_session.query(ApprovalRequest)
    if tenant_id is not None:
        q_req = q_req.filter(ApprovalRequest.tenant_id == tenant_id)
    if cloud_account_id is not None:
        q_req = q_req.filter(ApprovalRequest.cloud_account_id == cloud_account_id)

    req_ids = [r.id for r in q_req.all()]
    if req_ids:
        counts["approval_assignments"] = (
            db_session.query(ApprovalAssignment)
            .filter(ApprovalAssignment.approval_request_id.in_(req_ids))
            .delete(synchronize_session=False)
        )
        counts["approval_requests"] = q_req.delete(synchronize_session=False)
    elif not scoped:
        counts["approval_assignments"] = db_session.query(ApprovalAssignment).delete(synchronize_session=False)
        counts["approval_requests"] = db_session.query(ApprovalRequest).delete(synchronize_session=False)
    else:
        counts["approval_assignments"] = 0
        counts["approval_requests"] = 0

    nq = db_session.query(Notification).filter(Notification.type.in_(APPROVAL_NOTIFICATION_TYPES))
    if tenant_id is not None:
        tenant = db_session.query(Tenant).filter(Tenant.id == tenant_id).first()
        if tenant is None:
            counts["notifications"] = 0
        else:
            counts["notifications"] = (
                nq.filter(Notification.organization_id == tenant.organization_id).delete(synchronize_session=False)
            )
    else:
        counts["notifications"] = nq.delete(synchronize_session=False)

    db_session.commit()
    return counts


def reset_recommendation_outcome_workflow_for_resubmit(
    db_session: Session,
    *,
    tenant_id: UUID | None = None,
    cloud_account_id: UUID | None = None,
) -> int:
    """
    Set recommendation outcome(s) to ``workflow_status='suggested'``, ``status='pending'``, and clear
    approval / execution / savings-proof fields so **Submit for approval** is allowed again
    (see ``approval_request_service.create_approval_request``).

    Filters stack: pass ``tenant_id`` and/or ``cloud_account_id`` to limit scope. If neither filter is passed,
    every row is updated (local/dev only).

    Does not delete outcome rows or recommendations; use ``clear_ingested_data_for_cloud_account`` to wipe
    findings/recommendations and re-run sync for a full re-ingestion.
    """
    q = db_session.query(RecommendationOutcome)
    if tenant_id is not None:
        q = q.filter(RecommendationOutcome.tenant_id == tenant_id)
    if cloud_account_id is not None:
        q = q.filter(RecommendationOutcome.cloud_account_id == cloud_account_id)
    updated = q.update(
            {
                RecommendationOutcome.status: "pending",
                RecommendationOutcome.acted_on_at: None,
                RecommendationOutcome.workflow_status: "suggested",
                RecommendationOutcome.approved_by: None,
                RecommendationOutcome.approved_role: None,
                RecommendationOutcome.approved_membership_role: None,
                RecommendationOutcome.approved_access_role: None,
                RecommendationOutcome.approved_at: None,
                RecommendationOutcome.approval_comment: None,
                RecommendationOutcome.rejection_reason: None,
                RecommendationOutcome.rejected_at: None,
                RecommendationOutcome.rejected_by: None,
                RecommendationOutcome.applied_by: None,
                RecommendationOutcome.applied_role: None,
                RecommendationOutcome.applied_membership_role: None,
                RecommendationOutcome.applied_access_role: None,
                RecommendationOutcome.applied_at: None,
                RecommendationOutcome.execution_notes: None,
                RecommendationOutcome.preflight_passed_at: None,
                RecommendationOutcome.applied_via_auto: False,
                RecommendationOutcome.impact_status: None,
                RecommendationOutcome.impact_summary: None,
                RecommendationOutcome.follow_up_recommendation: None,
                RecommendationOutcome.last_evaluated_at: None,
                RecommendationOutcome.savings_verified_at: None,
                RecommendationOutcome.baseline_monthly_cost: None,
                RecommendationOutcome.current_monthly_cost: None,
                RecommendationOutcome.estimated_savings_at_action: None,
                RecommendationOutcome.realized_savings: None,
                RecommendationOutcome.before_cost: None,
                RecommendationOutcome.after_cost: None,
                RecommendationOutcome.estimated_savings: None,
            },
            synchronize_session=False,
        )
    db_session.commit()
    return updated
