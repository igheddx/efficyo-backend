from __future__ import annotations

from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.db import utc_now
from app.models.recommendation_outcome import RecommendationOutcome
from app.services import recommendation_outcome_service

# Thresholds for "still high" follow-ups (monthly USD)
_NAT_STILL_HIGH_USD = Decimal("10")
_AURORA_STILL_HIGH_USD = Decimal("50")


def effective_realized_savings(outcome: RecommendationOutcome) -> Optional[Decimal]:
    """Prefer stored realized_savings; otherwise derive from baseline and current."""
    if outcome.realized_savings is not None:
        return Decimal(str(outcome.realized_savings))
    if outcome.baseline_monthly_cost is not None and outcome.current_monthly_cost is not None:
        return Decimal(str(outcome.baseline_monthly_cost)) - Decimal(str(outcome.current_monthly_cost))
    return None


def classify_impact(realized: Optional[Decimal]) -> tuple[str, str]:
    """
    Map realized monthly savings to impact_status and impact_summary.

    Rules:
    - realized > 0  -> success
    - realized == 0 -> no_change
    - realized < 0  -> regression
    - realized is None -> no_change with an explanatory summary (insufficient data)
    """
    if realized is None:
        return (
            "no_change",
            "Unable to evaluate impact — baseline or current cost data is missing",
        )
    if realized > 0:
        return ("success", "Cost decreased after this change")
    if realized == 0:
        return ("no_change", "No measurable cost change detected")
    return ("regression", "Cost increased after this change")


def follow_up_recommendation_for(
    recommendation_type: str,
    impact_status: str,
    realized: Optional[Decimal],
    current_monthly_cost: Optional[Decimal],
) -> Optional[str]:
    """Type-specific follow-up text; optional fallback string."""
    rtype = (recommendation_type or "").lower()
    current = current_monthly_cost

    if rtype == "nat_gateway_cost_review":
        still_high = current is not None and current >= _NAT_STILL_HIGH_USD
        if impact_status in ("regression", "no_change") or still_high:
            return "Consider VPC endpoints or traffic routing optimization"
        return None

    if rtype == "lambda_rightsize_memory":
        if realized is None or realized <= 0:
            return "Profile actual memory usage before reducing allocation"
        return None

    if rtype == "aurora_serverless_cost_review":
        still_high = current is not None and current >= _AURORA_STILL_HIGH_USD
        if impact_status in ("regression", "no_change") or still_high:
            return "Review scaling configuration and connection patterns"
        return None

    return "Further optimization may be possible based on usage patterns"


def analyze_outcome_impact(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    outcome: RecommendationOutcome,
) -> RecommendationOutcome:
    """
    Recompute current costs vs baseline, classify impact, and set follow-up guidance.

    Updates: realized_savings, impact_status, impact_summary, follow_up_recommendation,
    last_evaluated_at.
    """
    if outcome.status not in {"acted_on", "verified"}:
        raise ValueError("outcome_not_actionable")

    updated = recommendation_outcome_service.recompute_realized_savings(
        db_session=db_session,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        outcome=outcome,
    )

    realized = effective_realized_savings(updated)
    impact_status, impact_summary = classify_impact(realized)

    current = None
    if updated.current_monthly_cost is not None:
        current = Decimal(str(updated.current_monthly_cost))

    follow_up = follow_up_recommendation_for(
        recommendation_type=updated.recommendation_type,
        impact_status=impact_status,
        realized=realized,
        current_monthly_cost=current,
    )

    updated.impact_status = impact_status
    updated.impact_summary = impact_summary
    updated.follow_up_recommendation = follow_up
    updated.last_evaluated_at = utc_now()
    # Verified is a system state after re-evaluation confirms positive impact on an applied change.
    if impact_status == "success" and updated.workflow_status == "applied":
        updated.workflow_status = "verified"
        updated.status = "verified"

    db_session.add(updated)
    db_session.commit()
    db_session.refresh(updated)
    return updated


def analyze_outcome_by_id(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    outcome_id: UUID,
) -> RecommendationOutcome:
    outcome = (
        db_session.query(RecommendationOutcome)
        .filter(
            RecommendationOutcome.id == outcome_id,
            RecommendationOutcome.tenant_id == tenant_id,
            RecommendationOutcome.cloud_account_id == cloud_account_id,
        )
        .first()
    )
    if outcome is None:
        raise ValueError("outcome_not_found")

    return analyze_outcome_impact(
        db_session=db_session,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        outcome=outcome,
    )


def re_evaluate_outcome(db_session: Session, outcome: RecommendationOutcome) -> RecommendationOutcome:
    """Re-check an acted-on outcome using the latest cost mapping.

    Updates current_monthly_cost, realized_savings, impact_status, impact_summary,
    and last_evaluated_at (and follows up with type-specific next steps).
    """
    return analyze_outcome_impact(
        db_session=db_session,
        tenant_id=outcome.tenant_id,
        cloud_account_id=outcome.cloud_account_id,
        outcome=outcome,
    )


def re_evaluate_outcome_by_id(db_session: Session, outcome_id: UUID) -> RecommendationOutcome:
    outcome = db_session.query(RecommendationOutcome).filter(RecommendationOutcome.id == outcome_id).first()
    if outcome is None:
        raise ValueError("outcome_not_found")

    return re_evaluate_outcome(db_session=db_session, outcome=outcome)
