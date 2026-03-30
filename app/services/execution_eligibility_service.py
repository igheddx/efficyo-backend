"""Derive execution / auto-execution eligibility from policy, approvals, and safety gates."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.recommendation import Recommendation
from app.models.recommendation_outcome import RecommendationOutcome
from app.services import approval_request_service, recommendation_outcome_service
from app.services.execution_constants import is_safe_auto_execution_type
from app.services.execution_policy_service import BUILTIN_POLICY, resolve_execution_policy


def _outcome_for_rec(
    db: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
) -> RecommendationOutcome | None:
    return (
        db.query(RecommendationOutcome)
        .filter(
            RecommendationOutcome.tenant_id == tenant_id,
            RecommendationOutcome.cloud_account_id == cloud_account_id,
            RecommendationOutcome.recommendation_id == recommendation_id,
        )
        .first()
    )


def compute_execution_eligibility(
    db: Session,
    *,
    organization_id: UUID,
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
) -> dict:
    rec = (
        db.query(Recommendation)
        .filter(
            Recommendation.id == recommendation_id,
            Recommendation.tenant_id == tenant_id,
            Recommendation.cloud_account_id == cloud_account_id,
        )
        .first()
    )
    if rec is None:
        return {
            "execution_eligible": False,
            "auto_execution_eligible": False,
            "blocking_reason": "recommendation_not_found",
            "effective_execution_mode": BUILTIN_POLICY.execution_mode,
            "policy_id": None,
            "policy_scope_level": None,
            "display_status": "blocked",
            "display_label": "Blocked",
            "preflight_required": False,
            "preflight_passed": False,
        }

    policy = resolve_execution_policy(
        db,
        organization_id=organization_id,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        recommendation_type=rec.recommendation_type,
        recommendation_risk_level=rec.risk_level,
    )

    outcome = _outcome_for_rec(db, tenant_id, cloud_account_id, recommendation_id)
    ws = recommendation_outcome_service.normalized_workflow_status(outcome.workflow_status if outcome else "suggested")
    rtype = (rec.recommendation_type or "").lower()
    safe_type = is_safe_auto_execution_type(rtype)

    preflight_passed = bool(outcome and outcome.preflight_passed_at is not None)
    applied_auto = bool(outcome and outcome.applied_via_auto)
    is_applied = bool(
        outcome
        and (
            outcome.status in ("acted_on", "verified")
            or ws in ("applied", "verified")
        )
    )

    if is_applied:
        return {
            "execution_eligible": False,
            "auto_execution_eligible": False,
            "blocking_reason": None,
            "effective_execution_mode": policy.execution_mode,
            "policy_id": str(policy.policy_row_id) if policy.policy_row_id else None,
            "policy_scope_level": policy.scope_level,
            "display_status": "auto_applied" if applied_auto else "applied_manually",
            "display_label": "Auto-applied" if applied_auto else "Executed (manual)",
            "preflight_required": policy.preflight_required,
            "preflight_passed": preflight_passed,
        }

    if ws == "rejected":
        return {
            "execution_eligible": False,
            "auto_execution_eligible": False,
            "blocking_reason": "workflow_rejected",
            "effective_execution_mode": policy.execution_mode,
            "policy_id": str(policy.policy_row_id) if policy.policy_row_id else None,
            "policy_scope_level": policy.scope_level,
            "display_status": "blocked",
            "display_label": "Blocked",
            "preflight_required": policy.preflight_required,
            "preflight_passed": preflight_passed,
        }

    if ws != "approved":
        return {
            "execution_eligible": False,
            "auto_execution_eligible": False,
            "blocking_reason": "approval_required",
            "effective_execution_mode": policy.execution_mode,
            "policy_id": str(policy.policy_row_id) if policy.policy_row_id else None,
            "policy_scope_level": policy.scope_level,
            "display_status": "blocked",
            "display_label": "Blocked",
            "preflight_required": policy.preflight_required,
            "preflight_passed": preflight_passed,
        }

    blocked, gate = approval_request_service.execution_blocked_by_approval_request(
        db, tenant_id, cloud_account_id, recommendation_id
    )
    if blocked:
        reason = gate or "approval_request_pending"
        return {
            "execution_eligible": False,
            "auto_execution_eligible": False,
            "blocking_reason": reason,
            "effective_execution_mode": policy.execution_mode,
            "policy_id": str(policy.policy_row_id) if policy.policy_row_id else None,
            "policy_scope_level": policy.scope_level,
            "display_status": "blocked",
            "display_label": "Blocked",
            "preflight_required": policy.preflight_required,
            "preflight_passed": preflight_passed,
        }

    if not safe_type:
        return {
            "execution_eligible": False,
            "auto_execution_eligible": False,
            "blocking_reason": "type_not_in_safe_execution_allowlist",
            "effective_execution_mode": policy.execution_mode,
            "policy_id": str(policy.policy_row_id) if policy.policy_row_id else None,
            "policy_scope_level": policy.scope_level,
            "display_status": "blocked",
            "display_label": "Blocked",
            "preflight_required": policy.preflight_required,
            "preflight_passed": preflight_passed,
        }

    if policy.preflight_required and not preflight_passed:
        return {
            "execution_eligible": False,
            "auto_execution_eligible": False,
            "blocking_reason": "preflight_required_not_passed",
            "effective_execution_mode": policy.execution_mode,
            "policy_id": str(policy.policy_row_id) if policy.policy_row_id else None,
            "policy_scope_level": policy.scope_level,
            "display_status": "blocked",
            "display_label": "Blocked",
            "preflight_required": True,
            "preflight_passed": False,
        }

    execution_eligible = True

    auto_eligible = (
        execution_eligible
        and policy.execution_mode == "approved_then_auto_allowed"
        and safe_type
    )

    if auto_eligible:
        display_status = "auto_execution_eligible"
        display_label = "Auto-execution eligible"
    elif policy.execution_mode == "manual_only":
        display_status = "manual_only"
        display_label = "Manual only"
    else:
        display_status = "ready_for_manual_execution"
        display_label = "Ready for manual execution"

    return {
        "execution_eligible": execution_eligible,
        "auto_execution_eligible": auto_eligible,
        "blocking_reason": None,
        "effective_execution_mode": policy.execution_mode,
        "policy_id": str(policy.policy_row_id) if policy.policy_row_id else None,
        "policy_scope_level": policy.scope_level,
        "display_status": display_status,
        "display_label": display_label,
        "preflight_required": policy.preflight_required,
        "preflight_passed": preflight_passed,
    }
