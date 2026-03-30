"""Insight narration layer for ftpNext (deterministic, no AI/LLMs)."""

from __future__ import annotations

from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.services import (
    recommendation_intelligence_service,
    recommendation_outcome_service,
    recommendation_service,
    summary_service,
    trend_service,
)


def _money(amount: float) -> str:
    # Input is expected to be a numeric float.
    if amount == amount and amount != 0:  # NaN-safe
        return f"${amount:,.2f}"
    return "$0.00"


def _clamp_pct(p: float) -> float:
    if p == p:
        return p
    return 0.0


def _choose_trend_sentences(trends: list[dict]) -> Optional[str]:
    increasing = [t for t in trends if t.get("trend") == "increasing" and t.get("percent_change") is not None]
    decreasing = [t for t in trends if t.get("trend") == "decreasing" and t.get("percent_change") is not None]

    top_inc = max(increasing, key=lambda t: float(t.get("percent_change", 0.0))) if increasing else None
    top_dec = min(decreasing, key=lambda t: float(t.get("percent_change", 0.0))) if decreasing else None  # most negative

    if top_inc and top_dec:
        return (
            f"{top_inc['service']} cost increased by {abs(float(top_inc['percent_change'])):.1f}% over the last week, "
            f"while {top_dec['service']} decreased by {abs(float(top_dec['percent_change'])):.1f}%."
        )

    if top_inc:
        return f"{top_inc['service']} cost increased by {abs(float(top_inc['percent_change'])):.1f}% over the last week."
    if top_dec:
        return f"{top_dec['service']} cost decreased by {abs(float(top_dec['percent_change'])):.1f}% over the last week."

    return "Most services are stable week over week (within 15%)."


def _outcome_sentence(outcomes: list) -> str:
    realized = [o for o in outcomes if o.get("realized_savings") is not None]
    if not realized:
        return "We have outcomes recorded, but no measurable before/after cost deltas yet."

    success = 0
    no_change = 0
    regression = 0
    for o in realized:
        v = o.get("realized_savings")
        if v is None:
            continue
        if v > 0:
            success += 1
        elif v == 0:
            no_change += 1
        else:
            regression += 1

    if regression > 0 and success > 0:
        return "Outcomes are mixed: some changes reduced cost, while others appear to have increased it."
    if success > 0 and regression == 0:
        return "Recent changes have started reducing costs in some areas."
    if regression > 0:
        return "Some recent changes may have increased cost and should be reviewed."

    # regression == 0 and success == 0
    return "Recent changes have not yet produced measurable savings."


def generate_insight_summary(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> dict:
    # Reuse existing building blocks (summary + trends + recommendations + outcomes).
    summary = summary_service.get_cloud_account_summary(db_session, tenant_id, cloud_account_id)
    trends = trend_service.detect_cost_trends(db_session, tenant_id, cloud_account_id)

    top_opportunities = recommendation_service.get_top_opportunities(
        db_session=db_session,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        limit=3,
    )

    outcomes = recommendation_outcome_service.list_outcomes(
        db_session=db_session,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
    )

    # Convert outcomes ORM objects into dict-like access for deterministic computations.
    outcomes_data = [
        {
            "realized_savings": float(o.realized_savings) if o.realized_savings is not None else None,
            "impact_status": o.impact_status,
        }
        for o in outcomes
    ]

    start_d = (summary.cost_period_start or "").strip()
    end_d = (summary.cost_period_end or "").strip()
    window = f"{start_d} through {end_d}" if start_d and end_d else "the last ~30 days in Cost Explorer"

    cost_overview = (
        f"{summary.cost_window_label}: AWS spend in Cost Explorer ({summary.cost_metric}) for {window} was {_money(float(summary.total_cost))}. "
        f"You identified approximately {_money(float(summary.total_estimated_monthly_savings))} in potential monthly savings "
        f"(~{_clamp_pct(float(summary.savings_percentage)):.1f}%)."
    )

    cost_basis_note = (
        f"Cost Explorer uses {summary.cost_window_label.lower()} and {summary.cost_metric}; the end date is exclusive in AWS. "
        "That total often differs slightly from your invoice (tax, credits, RI/SP amortization, or calendar billing periods)."
    )

    drivers = summary.top_cost_services[:2]
    if len(drivers) >= 2:
        cost_drivers = f"Your largest cost drivers are {drivers[0].service} ({_money(float(drivers[0].amount))}) and {drivers[1].service} ({_money(float(drivers[1].amount))})."
    elif len(drivers) == 1:
        cost_drivers = f"Your largest cost driver is {drivers[0].service} ({_money(float(drivers[0].amount))})."
    else:
        cost_drivers = "Top cost drivers will appear once Cost Explorer data is available."

    trend_sentence = _choose_trend_sentences(trends)
    outcome_sentence = _outcome_sentence(outcomes_data)

    if summary.top_savings_opportunity is not None:
        opp = summary.top_savings_opportunity
        opp_amount = (
            f" (~{_money(float(opp.estimated_savings))}/month)"
            if opp.estimated_savings is not None
            else ""
        )
        top_opportunity_sentence = f"Your biggest remaining opportunity is {opp.summary}{opp_amount}."
    else:
        top_opportunity_sentence = "You have no cost-savings opportunity identified right now."

    if summary.top_risk_issue is not None:
        risk = summary.top_risk_issue
        risk_sentence = f"A risk issue was detected: {risk.summary} (risk: {risk.risk_level})."
    else:
        risk_sentence = "No major risk issues were detected among the top recommendations."

    # Action focus (1-2 simple actions).
    action_focus = []
    if summary.top_savings_opportunity is not None:
        action_focus.append(f"Focus on {summary.top_savings_opportunity.summary}.")

    # Use the top increasing trend service (if any) to add a deterministic "why now".
    increasing = [t for t in trends if t.get("trend") == "increasing" and t.get("percent_change") is not None]
    if increasing:
        top_inc = max(increasing, key=lambda t: float(t.get("percent_change", 0.0)))
        action_focus.append(f"Investigate why {top_inc['service']} has been trending up recently.")
    else:
        action_focus.append("Review recent cost behavior and validate that the action is still aligned with usage.")

    if len(action_focus) > 2:
        action_focus = action_focus[:2]

    action_sentence = " ".join(action_focus)

    # Keep it to ~7-8 short sentences.
    summary_text = " ".join(
        [
            cost_overview,
            cost_drivers,
            trend_sentence or "",
            outcome_sentence,
            top_opportunity_sentence,
            risk_sentence,
            action_sentence,
        ]
    ).replace("  ", " ")

    return {
        "summary_text": summary_text,
        "cost_basis_note": cost_basis_note,
        "cost_window": summary.cost_window,
        "cost_window_label": summary.cost_window_label,
        "cost_metric": summary.cost_metric,
    }

