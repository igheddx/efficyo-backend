"""Lightweight learned confidence from historical recommendation outcomes (no ML)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.models.recommendation import Recommendation
from app.models.recommendation_outcome import RecommendationOutcome
from app.services.recommendation_credibility import effective_confidence_reason


@dataclass(frozen=True)
class RecommendationTypeStats:
    recommendation_type: str
    total_outcomes: int
    acted_on_count: int
    success_count: int
    no_change_count: int
    regression_count: int
    avg_realized_savings: float | None


def get_recommendation_type_stats(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> dict[str, RecommendationTypeStats]:
    """
    Aggregate outcome rows by recommendation_type for the cloud account.

    Counts use ``impact_status`` when present (success / no_change / regression).
    ``acted_on_count`` is outcomes marked ``acted_on`` or ``verified``.
    """
    rows = (
        db_session.query(
            RecommendationOutcome.recommendation_type.label("rtype"),
            func.count().label("total_outcomes"),
            func.sum(
                case(
                    (RecommendationOutcome.status.in_(["acted_on", "verified"]), 1),
                    else_=0,
                )
            ).label("acted_on_count"),
            func.sum(
                case((RecommendationOutcome.impact_status == "success", 1), else_=0)
            ).label("success_count"),
            func.sum(
                case((RecommendationOutcome.impact_status == "no_change", 1), else_=0)
            ).label("no_change_count"),
            func.sum(
                case((RecommendationOutcome.impact_status == "regression", 1), else_=0)
            ).label("regression_count"),
            func.avg(RecommendationOutcome.realized_savings).label("avg_realized"),
        )
        .filter(
            RecommendationOutcome.tenant_id == tenant_id,
            RecommendationOutcome.cloud_account_id == cloud_account_id,
        )
        .group_by(RecommendationOutcome.recommendation_type)
        .all()
    )

    out: dict[str, RecommendationTypeStats] = {}
    for row in rows:
        rtype = row.rtype or ""
        avg_val = row.avg_realized
        avg_float: float | None
        if avg_val is None:
            avg_float = None
        else:
            avg_float = float(Decimal(str(avg_val)).quantize(Decimal("0.01")))

        out[rtype] = RecommendationTypeStats(
            recommendation_type=rtype,
            total_outcomes=int(row.total_outcomes or 0),
            acted_on_count=int(row.acted_on_count or 0),
            success_count=int(row.success_count or 0),
            no_change_count=int(row.no_change_count or 0),
            regression_count=int(row.regression_count or 0),
            avg_realized_savings=avg_float,
        )
    return out


def learned_intelligence_for_recommendation(
    rec: Recommendation,
    stats_by_type: dict[str, RecommendationTypeStats],
) -> dict[str, Any]:
    """
    Return API fields: learned_confidence, learned_confidence_reason,
    historical_success_rate, avg_realized_savings_for_type.
    """
    stats = stats_by_type.get(rec.recommendation_type)
    existing_reason = effective_confidence_reason(rec) or ""

    if stats is None or stats.total_outcomes == 0:
        return {
            "learned_confidence": rec.confidence_score,
            "learned_confidence_reason": existing_reason,
            "historical_success_rate": None,
            "avg_realized_savings_for_type": None,
        }

    ac = stats.acted_on_count
    sc = stats.success_count
    nc = stats.no_change_count
    rg = stats.regression_count

    historical_success_rate: float | None = None
    if ac > 0:
        historical_success_rate = round(sc / ac, 4)

    avg_for_type = stats.avg_realized_savings

    # Priority: regression → strong success → mixed → fallback
    if rg >= 2:
        return {
            "learned_confidence": "low",
            "learned_confidence_reason": (
                "This recommendation type has shown limited or negative results in recent outcomes"
            ),
            "historical_success_rate": historical_success_rate,
            "avg_realized_savings_for_type": avg_for_type,
        }

    if sc >= 3 and ac > 0 and (sc / ac) >= 0.7:
        return {
            "learned_confidence": "high",
            "learned_confidence_reason": (
                "This recommendation type has shown positive savings in similar cases"
            ),
            "historical_success_rate": historical_success_rate,
            "avg_realized_savings_for_type": avg_for_type,
        }

    if ac >= 3 and ac > 0 and (nc / ac) >= 0.5:
        return {
            "learned_confidence": "medium",
            "learned_confidence_reason": "This recommendation type has shown mixed results so far",
            "historical_success_rate": historical_success_rate,
            "avg_realized_savings_for_type": avg_for_type,
        }

    return {
        "learned_confidence": rec.confidence_score,
        "learned_confidence_reason": existing_reason,
        "historical_success_rate": historical_success_rate,
        "avg_realized_savings_for_type": avg_for_type,
    }
