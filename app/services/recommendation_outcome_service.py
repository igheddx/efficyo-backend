from __future__ import annotations

from decimal import Decimal
import logging
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.cost_window import account_cost_window_label, round_currency_decimal
from app.core.db import utc_now
from app.cost import query_service
from app.models.recommendation import Recommendation
from app.models.recommendation_outcome import RecommendationOutcome
from app.models.tenant import Tenant
from app.services import cloud_account_service


logger = logging.getLogger(__name__)


def normalized_workflow_status(value: str | None) -> str:
    # Backward compatibility for historical rows created before status simplification.
    if value == "generated":
        return "approved"
    if value == "rejected":
        return "rejected"
    return value or "suggested"


def _get_recommendation_or_raise(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
) -> Recommendation:
    recommendation = (
        db_session.query(Recommendation)
        .filter(
            Recommendation.tenant_id == tenant_id,
            Recommendation.cloud_account_id == cloud_account_id,
            Recommendation.id == recommendation_id,
        )
        .first()
    )
    if recommendation is None:
        raise ValueError("recommendation_not_found")
    return recommendation


def _nat_gateway_amount_from_ec2_other_breakdown(ec2_other_breakdown: dict) -> Optional[Decimal]:
    try:
        breakdown = ec2_other_breakdown.get("breakdown") or []
        nat = next(
            (
                Decimal(str(item.get("amount", 0.0)))
                for item in breakdown
                if item.get("category") == "NAT Gateway"
            ),
            None,
        )
        return nat
    except Exception:
        return None


def _service_amount_from_cost_summary(cost_summary: dict, target_service_names: list[str]) -> Optional[Decimal]:
    for item in cost_summary.get("by_service") or []:
        name = (item.get("service") or "").strip()
        amount = item.get("amount")
        if amount is None:
            continue
        if name in target_service_names:
            return Decimal(str(amount))
    return None


def _rolling_30d_account_total(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> tuple[Decimal | None, str]:
    """(total UnblendedCost for rolling 30d, human-readable window label)."""
    cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)
    summary = query_service.get_summary(db_session, tenant_id=tenant_id, cloud_account_id=cloud_account_id)
    return Decimal(str(summary.get("total_cost", 0.0))), account_cost_window_label()


def set_proof_before_cost_if_missing(
    db_session: Session,
    outcome: RecommendationOutcome,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> bool:
    """Set before_cost from rolling 30d account total if not already captured. Returns True if updated."""
    if outcome.before_cost is not None:
        return False
    total, _label = _rolling_30d_account_total(db_session, tenant_id, cloud_account_id)
    if total is None:
        return False
    outcome.before_cost = round_currency_decimal(total)
    return True


def _apply_proof_after_snapshot(
    db_session: Session,
    outcome: RecommendationOutcome,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> None:
    """After execution, store rolling 30d total as after_cost and derived estimated_savings."""
    total, _label = _rolling_30d_account_total(db_session, tenant_id, cloud_account_id)
    if total is None:
        return
    outcome.after_cost = round_currency_decimal(total)
    if outcome.before_cost is not None:
        b = Decimal(str(outcome.before_cost))
        a = Decimal(str(outcome.after_cost))
        outcome.estimated_savings = round_currency_decimal(b - a)
        # Estimated-first: align legacy realized field with proof estimate when we have a proof pair.
        outcome.realized_savings = outcome.estimated_savings


def _baseline_monthly_cost_for_recommendation_type(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation: Recommendation,
) -> Optional[Decimal]:
    cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)
    rtype = (recommendation.recommendation_type or "").lower()

    if rtype == "nat_gateway_cost_review":
        try:
            ec2_other = query_service.get_ec2_other_breakdown(
                db_session, tenant_id=tenant_id, cloud_account_id=cloud_account_id
            )
            return _nat_gateway_amount_from_ec2_other_breakdown(ec2_other)
        except Exception:
            logger.exception("Failed to fetch NAT Gateway baseline monthly cost")
            return None

    if rtype == "aurora_serverless_cost_review":
        try:
            summary = query_service.get_summary(db_session, tenant_id=tenant_id, cloud_account_id=cloud_account_id)
            # Cost Explorer uses this service label in the current implementation.
            return _service_amount_from_cost_summary(
                summary,
                target_service_names=["Amazon Relational Database Service"],
            )
        except Exception:
            logger.exception("Failed to fetch RDS baseline monthly cost for Aurora recommendations")
            return None

    if rtype == "lambda_rightsize_memory":
        # For now, use the recommendation's deterministic estimated savings.
        if recommendation.estimated_savings is None:
            return None
        return Decimal(str(recommendation.estimated_savings))

    return None


def _current_monthly_cost_for_recommendation_type(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    outcome: RecommendationOutcome,
) -> Optional[Decimal]:
    cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)
    rtype = (outcome.recommendation_type or "").lower()

    if rtype == "nat_gateway_cost_review":
        try:
            ec2_other = query_service.get_ec2_other_breakdown(
                db_session, tenant_id=tenant_id, cloud_account_id=cloud_account_id
            )
            return _nat_gateway_amount_from_ec2_other_breakdown(ec2_other)
        except Exception:
            logger.exception("Failed to fetch NAT Gateway current monthly cost")
            return None

    if rtype == "aurora_serverless_cost_review":
        try:
            summary = query_service.get_summary(db_session, tenant_id=tenant_id, cloud_account_id=cloud_account_id)
            return _service_amount_from_cost_summary(
                summary,
                target_service_names=["Amazon Relational Database Service"],
            )
        except Exception:
            logger.exception("Failed to fetch RDS current monthly cost for Aurora recommendations")
            return None

    if rtype == "lambda_rightsize_memory":
        try:
            summary = query_service.get_summary(db_session, tenant_id=tenant_id, cloud_account_id=cloud_account_id)
            direct = _service_amount_from_cost_summary(summary, target_service_names=["AWS Lambda"])
            if direct is not None:
                return direct
        except Exception:
            logger.exception("Failed to fetch AWS Lambda current monthly cost")
            return None

        # Fallback to deterministic placeholder.
        if outcome.estimated_savings_at_action is not None:
            return Decimal(str(outcome.estimated_savings_at_action))
        return None

    return None


def create_outcome_for_recommendation(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
    notes: Optional[str] = None,
) -> RecommendationOutcome:
    recommendation = _get_recommendation_or_raise(db_session, tenant_id, cloud_account_id, recommendation_id)

    existing = (
        db_session.query(RecommendationOutcome)
        .filter(RecommendationOutcome.recommendation_id == recommendation.id)
        .first()
    )
    if existing is not None:
        return existing

    outcome = RecommendationOutcome(
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        recommendation_id=recommendation.id,
        resource_id=recommendation.resource_id,
        recommendation_type=recommendation.recommendation_type,
        recommendation_category=recommendation.recommendation_category,
        status="pending",
        workflow_status="suggested",
        notes=notes,
    )
    db_session.add(outcome)
    db_session.commit()
    db_session.refresh(outcome)
    outcome.workflow_status = normalized_workflow_status(outcome.workflow_status)
    return outcome


def record_preflight_passed_if_ready(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
    *,
    aggregate_status: str,
) -> None:
    """Persist last successful preflight when checks aggregate to ``ready``."""
    if (aggregate_status or "").strip().lower() != "ready":
        return
    existing = (
        db_session.query(RecommendationOutcome)
        .filter(
            RecommendationOutcome.tenant_id == tenant_id,
            RecommendationOutcome.cloud_account_id == cloud_account_id,
            RecommendationOutcome.recommendation_id == recommendation_id,
        )
        .first()
    )
    if existing is None:
        existing = create_outcome_for_recommendation(
            db_session=db_session,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            recommendation_id=recommendation_id,
        )
    existing.preflight_passed_at = utc_now()
    db_session.add(existing)
    db_session.commit()


def mark_recommendation_acted_on(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
    notes: Optional[str] = None,
) -> RecommendationOutcome:
    recommendation = _get_recommendation_or_raise(db_session, tenant_id, cloud_account_id, recommendation_id)
    existing = (
        db_session.query(RecommendationOutcome)
        .filter(
            RecommendationOutcome.tenant_id == tenant_id,
            RecommendationOutcome.cloud_account_id == cloud_account_id,
            RecommendationOutcome.recommendation_id == recommendation_id,
        )
        .first()
    )

    if existing is None:
        existing = create_outcome_for_recommendation(
            db_session=db_session,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            recommendation_id=recommendation_id,
            notes=notes,
        )

    baseline_cost = _baseline_monthly_cost_for_recommendation_type(
        db_session=db_session,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        recommendation=recommendation,
    )

    existing.status = "acted_on"
    existing.acted_on_at = utc_now()
    if existing.workflow_status == "suggested":
        existing.workflow_status = "applied"
    existing.applied_at = existing.applied_at or existing.acted_on_at
    existing.estimated_savings_at_action = recommendation.estimated_savings
    existing.baseline_monthly_cost = baseline_cost
    existing.notes = notes if notes is not None else existing.notes

    recommendation.state = "resolved"

    _apply_proof_after_snapshot(db_session, existing, tenant_id, cloud_account_id)

    # current_monthly_cost + legacy realized_savings refreshed when outcomes are listed if no proof pair.
    db_session.add(existing)
    db_session.commit()
    db_session.refresh(existing)
    existing.workflow_status = normalized_workflow_status(existing.workflow_status)
    return existing


def approve_recommendation(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
    approved_by: Optional[str] = None,
    approved_role: Optional[str] = None,
    approval_comment: Optional[str] = None,
    *,
    approved_membership_role: Optional[str] = None,
    approved_access_role: Optional[str] = None,
) -> RecommendationOutcome:
    recommendation = _get_recommendation_or_raise(db_session, tenant_id, cloud_account_id, recommendation_id)
    outcome = (
        db_session.query(RecommendationOutcome)
        .filter(
            RecommendationOutcome.tenant_id == tenant_id,
            RecommendationOutcome.cloud_account_id == cloud_account_id,
            RecommendationOutcome.recommendation_id == recommendation.id,
        )
        .first()
    )
    if outcome is None:
        outcome = create_outcome_for_recommendation(
            db_session=db_session,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            recommendation_id=recommendation_id,
        )

    outcome.workflow_status = "approved"
    outcome.approved_by = approved_by if approved_by is not None else outcome.approved_by
    outcome.approved_role = approved_role if approved_role is not None else outcome.approved_role
    if approved_membership_role is not None:
        outcome.approved_membership_role = approved_membership_role
    if approved_access_role is not None:
        outcome.approved_access_role = approved_access_role
    outcome.approved_at = utc_now()
    outcome.rejection_reason = None
    outcome.rejected_at = None
    outcome.rejected_by = None
    if approval_comment is not None:
        t = approval_comment.strip()
        outcome.approval_comment = t if t else None
    set_proof_before_cost_if_missing(db_session, outcome, tenant_id, cloud_account_id)
    db_session.add(outcome)
    db_session.commit()
    db_session.refresh(outcome)
    outcome.workflow_status = normalized_workflow_status(outcome.workflow_status)
    try:
        from app.services import notification_service

        summary = notification_service.recommendation_summary(
            db_session, tenant_id, cloud_account_id, recommendation_id
        )
        notification_service.notify_approval_completed(
            db_session,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            recommendation_id=recommendation_id,
            summary=summary,
        )
    except Exception:
        logger.debug("approval_completed notification skipped", exc_info=True)

    return outcome


def reject_recommendation(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
    *,
    rejection_reason: str,
    rejected_by: Optional[str] = None,
) -> RecommendationOutcome:
    recommendation = _get_recommendation_or_raise(db_session, tenant_id, cloud_account_id, recommendation_id)
    reason = (rejection_reason or "").strip()
    if not reason:
        raise ValueError("rejection_reason_required")

    outcome = (
        db_session.query(RecommendationOutcome)
        .filter(
            RecommendationOutcome.tenant_id == tenant_id,
            RecommendationOutcome.cloud_account_id == cloud_account_id,
            RecommendationOutcome.recommendation_id == recommendation.id,
        )
        .first()
    )
    if outcome is None:
        outcome = create_outcome_for_recommendation(
            db_session=db_session,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            recommendation_id=recommendation_id,
        )

    cur = normalized_workflow_status(outcome.workflow_status)
    if cur in {"applied", "verified"}:
        raise ValueError("cannot_reject_applied_or_verified")

    outcome.workflow_status = "rejected"
    outcome.rejection_reason = reason
    outcome.rejected_at = utc_now()
    outcome.rejected_by = rejected_by
    outcome.approved_by = None
    outcome.approved_role = None
    outcome.approved_at = None
    outcome.approval_comment = None
    db_session.add(outcome)
    db_session.commit()
    db_session.refresh(outcome)
    outcome.workflow_status = normalized_workflow_status(outcome.workflow_status)
    return outcome


def mark_recommendation_applied(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
    applied_by: Optional[str] = None,
    applied_role: Optional[str] = None,
    execution_notes: Optional[str] = None,
    *,
    applied_membership_role: Optional[str] = None,
    applied_access_role: Optional[str] = None,
    applied_via_auto: bool = False,
) -> RecommendationOutcome:
    outcome = mark_recommendation_acted_on(
        db_session=db_session,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        recommendation_id=recommendation_id,
        notes=execution_notes,
    )
    outcome.workflow_status = "applied"
    outcome.applied_by = applied_by if applied_by is not None else outcome.applied_by
    outcome.applied_role = applied_role if applied_role is not None else outcome.applied_role
    if applied_membership_role is not None:
        outcome.applied_membership_role = applied_membership_role
    if applied_access_role is not None:
        outcome.applied_access_role = applied_access_role
    outcome.applied_at = utc_now()
    outcome.execution_notes = execution_notes if execution_notes is not None else outcome.execution_notes
    if applied_via_auto:
        outcome.applied_via_auto = True
    db_session.add(outcome)
    db_session.commit()
    db_session.refresh(outcome)
    outcome.workflow_status = normalized_workflow_status(outcome.workflow_status)
    try:
        from app.services import notification_service

        summary = notification_service.recommendation_summary(
            db_session, tenant_id, cloud_account_id, recommendation_id
        )
        notification_service.notify_execution_completed(
            db_session,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            recommendation_id=recommendation_id,
            summary=summary,
        )
    except Exception:
        logger.debug("execution_completed notification skipped", exc_info=True)

    return outcome


def recompute_realized_savings(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    outcome: RecommendationOutcome,
) -> RecommendationOutcome:
    # Only attempt recompute for records that already have an action baseline.
    if outcome.status not in {"acted_on", "verified"}:
        return outcome

    if outcome.before_cost is not None:
        _apply_proof_after_snapshot(db_session, outcome, tenant_id, cloud_account_id)
        db_session.add(outcome)
        db_session.commit()
        db_session.refresh(outcome)
        return outcome

    if outcome.baseline_monthly_cost is None:
        return outcome

    current = _current_monthly_cost_for_recommendation_type(
        db_session=db_session,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        outcome=outcome,
    )
    # If we can't map the latest cost (or the lookup fails), preserve stored values.
    if current is not None and outcome.baseline_monthly_cost is not None:
        outcome.current_monthly_cost = current
        baseline = Decimal(str(outcome.baseline_monthly_cost))
        now = Decimal(str(current))
        outcome.realized_savings = baseline - now

    db_session.add(outcome)
    db_session.commit()
    db_session.refresh(outcome)
    return outcome


def list_outcomes(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> list[RecommendationOutcome]:
    rows = (
        db_session.query(RecommendationOutcome)
        .filter(
            RecommendationOutcome.tenant_id == tenant_id,
            RecommendationOutcome.cloud_account_id == cloud_account_id,
        )
        .order_by(RecommendationOutcome.created_at.desc())
        .all()
    )
    for r in rows:
        r.workflow_status = normalized_workflow_status(r.workflow_status)
    return rows


def list_workflow_rows(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> list[dict]:
    rows = (
        db_session.query(RecommendationOutcome, Recommendation.summary)
        .join(Recommendation, RecommendationOutcome.recommendation_id == Recommendation.id)
        .filter(
            RecommendationOutcome.tenant_id == tenant_id,
            RecommendationOutcome.cloud_account_id == cloud_account_id,
        )
        .order_by(RecommendationOutcome.updated_at.desc())
        .all()
    )

    return [
        {
            "recommendation_id": outcome.recommendation_id,
            "recommendation_summary": summary,
            "recommendation_type": outcome.recommendation_type,
            "workflow_status": normalized_workflow_status(outcome.workflow_status),
            "approved_by": outcome.approved_by,
            "approved_role": outcome.approved_role,
            "approved_at": outcome.approved_at,
            "applied_by": outcome.applied_by,
            "applied_role": outcome.applied_role,
            "applied_at": outcome.applied_at,
            "execution_notes": outcome.execution_notes,
            "realized_savings": float(outcome.realized_savings) if outcome.realized_savings is not None else None,
        }
        for outcome, summary in rows
    ]


def list_workflow_timeline(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> list[dict]:
    rows = (
        db_session.query(
            RecommendationOutcome,
            Recommendation.summary,
            Recommendation.recommendation_category,
        )
        .join(Recommendation, RecommendationOutcome.recommendation_id == Recommendation.id)
        .filter(
            RecommendationOutcome.tenant_id == tenant_id,
            RecommendationOutcome.cloud_account_id == cloud_account_id,
        )
        .order_by(RecommendationOutcome.updated_at.desc())
        .all()
    )
    return [
        {
            "recommendation_id": outcome.recommendation_id,
            "summary": summary,
            "recommendation_category": recommendation_category,
            "workflow_status": normalized_workflow_status(outcome.workflow_status),
            "approved_by": outcome.approved_by,
            "approved_role": outcome.approved_role,
            "approved_at": outcome.approved_at,
            "applied_by": outcome.applied_by,
            "applied_role": outcome.applied_role,
            "applied_at": outcome.applied_at,
            "execution_notes": outcome.execution_notes,
            "impact_status": outcome.impact_status,
            "realized_savings": float(outcome.realized_savings) if outcome.realized_savings is not None else None,
            "last_evaluated_at": outcome.last_evaluated_at,
            "before_cost": float(outcome.before_cost) if outcome.before_cost is not None else None,
            "after_cost": float(outcome.after_cost) if outcome.after_cost is not None else None,
            "estimated_savings": float(outcome.estimated_savings) if outcome.estimated_savings is not None else None,
            "savings_verified_at": outcome.savings_verified_at,
        }
        for outcome, summary, recommendation_category in rows
    ]


def savings_proof_summary_for_cloud_account(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> dict:
    """Sum proof-layer estimated savings (rolling 30d account before/after) for this cloud account."""
    from sqlalchemy import func

    row = (
        db_session.query(
            func.count(RecommendationOutcome.id),
            func.coalesce(func.sum(RecommendationOutcome.estimated_savings), 0),
        )
        .filter(
            RecommendationOutcome.tenant_id == tenant_id,
            RecommendationOutcome.cloud_account_id == cloud_account_id,
            RecommendationOutcome.estimated_savings.isnot(None),
        )
        .first()
    )
    n, total = row[0], row[1]
    return {
        "outcomes_with_estimate_count": int(n or 0),
        "total_estimated_monthly_savings_proof": float(total or 0),
        "cost_window_label": account_cost_window_label(),
        "cost_metric": "UnblendedCost",
    }


def savings_proof_summary_for_tenant(db_session: Session, tenant_id: UUID) -> dict:
    """Aggregate proof estimates across all cloud accounts in a tenant."""
    from sqlalchemy import func

    row = (
        db_session.query(
            func.count(RecommendationOutcome.id),
            func.coalesce(func.sum(RecommendationOutcome.estimated_savings), 0),
        )
        .filter(
            RecommendationOutcome.tenant_id == tenant_id,
            RecommendationOutcome.estimated_savings.isnot(None),
        )
        .first()
    )
    n, total = row[0], row[1]
    return {
        "outcomes_with_estimate_count": int(n or 0),
        "total_estimated_monthly_savings_proof": float(total or 0),
        "cost_window_label": account_cost_window_label(),
        "cost_metric": "UnblendedCost",
    }


def savings_proof_summary_for_organization(db_session: Session, organization_id: UUID) -> dict:
    """Aggregate proof estimates for all tenants in an MSP organization."""
    from sqlalchemy import func

    row = (
        db_session.query(
            func.count(RecommendationOutcome.id),
            func.coalesce(func.sum(RecommendationOutcome.estimated_savings), 0),
        )
        .join(Tenant, Tenant.id == RecommendationOutcome.tenant_id)
        .filter(
            Tenant.organization_id == organization_id,
            RecommendationOutcome.estimated_savings.isnot(None),
        )
        .first()
    )
    n, total = row[0], row[1]
    return {
        "outcomes_with_estimate_count": int(n or 0),
        "total_estimated_monthly_savings_proof": float(total or 0),
        "cost_window_label": account_cost_window_label(),
        "cost_metric": "UnblendedCost",
    }


def workflow_progress_summary(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> dict:
    rows = (
        db_session.query(RecommendationOutcome)
        .filter(
            RecommendationOutcome.tenant_id == tenant_id,
            RecommendationOutcome.cloud_account_id == cloud_account_id,
        )
        .all()
    )
    approved = 0
    applied = 0
    verified = 0
    pending_verification = 0
    realized_total = Decimal("0.00")

    for row in rows:
        status = normalized_workflow_status(row.workflow_status)
        if status == "approved":
            approved += 1
        elif status == "applied":
            applied += 1
            pending_verification += 1
        elif status == "verified":
            verified += 1
            if row.realized_savings is not None:
                realized_total += Decimal(str(row.realized_savings))

    return {
        "verified_recommendations_count": verified,
        "applied_recommendations_count": applied,
        "approved_recommendations_count": approved,
        "realized_monthly_savings_total": float(realized_total),
        "pending_verification_count": pending_verification,
    }

