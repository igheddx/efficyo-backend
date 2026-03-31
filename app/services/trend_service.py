"""Week-over-week cost trend detection using Cost Explorer DAILY + SERVICE (last 14 days)."""

from __future__ import annotations

from decimal import Decimal
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.recommendation_outcome import RecommendationOutcome
from app.core.cost_window import (
    account_cost_window_fields,
    rolling_nd_window_fields,
    round_currency,
    savings_outcomes_nd_window_fields,
    utc_today,
)
from app.cost import query_service
from app.services import cloud_account_service, recommendation_service


def _sum_services_for_days(
    days: list[dict[str, Any]],
) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for day in days:
        for service, amount in (day.get("by_service") or {}).items():
            totals[service] = totals.get(service, Decimal("0.00")) + Decimal(amount)
    return totals


def _percent_change_and_trend(previous_cost: Decimal, current_cost: Decimal) -> tuple[float, str]:
    delta = current_cost - previous_cost

    if previous_cost > 0:
        percent_change = float((delta / previous_cost) * Decimal("100"))
    elif current_cost > Decimal("0"):
        percent_change = 100.0
    else:
        percent_change = 0.0

    if percent_change > 15.0:
        trend = "increasing"
    elif percent_change < -15.0:
        trend = "decreasing"
    else:
        trend = "stable"

    return (round(percent_change, 2), trend)


def _summary_line(service: str, trend: str, percent_change: float) -> str:
    pct = abs(percent_change)
    pct_s = f"{pct:.1f}".rstrip("0").rstrip(".")
    if trend == "increasing":
        return f"{service} cost increased by {pct_s}% over the last week"
    if trend == "decreasing":
        return f"{service} cost decreased by {pct_s}% over the last week"
    return f"{service} cost was stable week over week (within 15%)"


def detect_cost_trends(db_session: Session, tenant_id: UUID, cloud_account_id: UUID) -> list[dict[str, Any]]:
    """
    Compare last 7 days vs previous 7 days of DAILY Cost Explorer data (grouped by SERVICE).

    Returns a list of trend dicts sorted by service name; callers may re-order for display.
    """
    cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)
    return query_service.get_wow_trends(db_session, tenant_id, cloud_account_id)


def cost_trends_over_time(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    days: int = 30,
) -> dict[str, Any]:
    """
    Daily total AWS cost trend for the last ``days`` using Cost Explorer DAILY + SERVICE,
    aggregated to one total per date.
    """
    cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)
    window_days = max(1, int(days))
    data = query_service.get_cost_trends(db_session, tenant_id, cloud_account_id)
    meta = account_cost_window_fields() if window_days == 30 else rolling_nd_window_fields(window_days)
    return {"days": window_days, "points": data.get("points", []), **meta}


def savings_trends_over_time(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    days: int = 30,
) -> dict[str, Any]:
    """
    Daily realized savings trend derived from verified outcomes.
    Uses last_evaluated_at/acted_on_at/updated_at as event date and realized_savings as value.
    """
    cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)
    window_days = max(1, int(days))
    today = utc_today()
    start_day = today - timedelta(days=window_days - 1)
    day_keys = [(start_day + timedelta(days=i)).isoformat() for i in range(window_days)]
    realized_by_day: dict[str, Decimal] = {d: Decimal("0.00") for d in day_keys}

    outcomes = (
        db_session.query(RecommendationOutcome)
        .filter(
            RecommendationOutcome.tenant_id == tenant_id,
            RecommendationOutcome.cloud_account_id == cloud_account_id,
            RecommendationOutcome.status == "verified",
        )
        .all()
    )

    for outcome in outcomes:
        if outcome.realized_savings is None:
            continue
        ts = outcome.last_evaluated_at or outcome.acted_on_at or outcome.updated_at
        if ts is None:
            continue
        k = ts.date().isoformat()
        if k in realized_by_day:
            realized_by_day[k] += Decimal(str(outcome.realized_savings))

    points = [
        {
            "date": d,
            "savings_realized": round_currency(realized_by_day[d]),
        }
        for d in day_keys
    ]

    rec_map = {
        r.id: r.summary
        for r in recommendation_service.list_recommendations(
            db_session, tenant_id, cloud_account_id, latest_only=True
        )
    }
    before_after = [
        {
            "recommendation_id": o.recommendation_id,
            "summary": rec_map.get(o.recommendation_id),
            "before_cost": (
                round_currency(o.baseline_monthly_cost) if o.baseline_monthly_cost is not None else None
            ),
            "after_cost": (
                round_currency(o.current_monthly_cost) if o.current_monthly_cost is not None else None
            ),
            "savings": round_currency(o.realized_savings) if o.realized_savings is not None else None,
        }
        for o in outcomes
    ]
    before_after.sort(key=lambda x: (x["savings"] is None, -(x["savings"] or 0.0)))

    return {
        "days": window_days,
        "points": points,
        "before_after": before_after,
        **savings_outcomes_nd_window_fields(window_days),
    }
