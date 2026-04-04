"""Recommendation credibility copy and resource-aware prioritization helpers."""

from __future__ import annotations

from decimal import Decimal
from typing import Optional
from uuid import UUID

from app.models.recommendation import Recommendation
from app.core.cost_window import ACCOUNT_ROLLING_WINDOW_DAYS, DEFAULT_COST_METRIC, account_cost_window_label
from app.services.recommendation_scoring import (
    computed_priority_score,
    priority_bucket_for as priority_bucket_for_recommendation,
    ranking_reason_for as ranking_reason_from_profile,
    recommendation_sort_key,
    resolved_scoring_profile,
    score_value,
)


def savings_basis_for(recommendation_type: str, recommendation_category: str) -> str:
    """Rule order: specific types override generic cost category."""
    rtype = (recommendation_type or "").lower()
    cat = (recommendation_category or "").lower()

    if "lambda" in rtype:
        return "Based on memory configuration and estimated utilization"
    if "nat" in rtype:
        return (
            f"Based on EC2 Other (NAT Gateway) cost ({account_cost_window_label()}, {DEFAULT_COST_METRIC})"
        )
    if "waf" in rtype:
        return "Based on AWS WAF usage and request volume"
    if cat == "cost":
        return f"Based on last {ACCOUNT_ROLLING_WINDOW_DAYS} days of usage ({DEFAULT_COST_METRIC})"
    return "Heuristic estimate based on configuration"


def confidence_reason_for(estimated_savings: Optional[Decimal | float]) -> str:
    if estimated_savings is None:
        return "No direct cost savings, but improves security or governance"
    try:
        val = float(estimated_savings)
    except (TypeError, ValueError):
        return "Heuristic estimate based on configuration"
    if val >= 20:
        return "High cost impact with consistent usage"
    if val >= 5:
        return "Moderate cost impact based on recent usage"
    return "Low cost impact or variable usage"


def why_it_matters_for(rank: int | None, recommendation_category: str) -> str:
    cat = (recommendation_category or "").lower()
    if cat == "security":
        return "This exposes your environment to potential risk"
    if cat == "governance":
        return "Improves visibility and cost tracking across resources"
    if cat == "cost" and rank is not None and rank in (1, 2, 3):
        return f"This is your #{rank} cost driver this month"
    return "This can improve efficiency and reduce unnecessary spend"


def _risk_factor(risk_level: str) -> float:
    level = (risk_level or "").lower()
    if level == "high":
        return 1.0
    if level == "medium":
        return 0.6
    if level == "low":
        return 0.3
    return 0.3


def _confidence_factor(confidence_score: str) -> float:
    score = (confidence_score or "").lower()
    if score == "high":
        return 1.0
    if score == "medium":
        return 0.7
    if score == "low":
        return 0.4
    return 0.4


def _urgency_factor(recommendation_category: str) -> float:
    cat = (recommendation_category or "").lower()
    if cat == "security":
        return 1.0
    if cat == "cost":
        return 0.7
    if cat == "governance":
        return 0.4
    return 0.4


def _normalized_savings(estimated_savings: Optional[Decimal | float], max_savings: float) -> float:
    if max_savings <= 0:
        return 0.0
    if estimated_savings is None:
        return 0.0
    try:
        val = float(estimated_savings)
    except (TypeError, ValueError):
        return 0.0
    if val <= 0:
        return 0.0
    return min(1.0, max(0.0, val / max_savings))


def decision_factors_for(rec: Recommendation, max_savings: float) -> dict[str, float]:
    normalized_savings = _normalized_savings(rec.estimated_savings, max_savings)
    profile = resolved_scoring_profile(rec)
    risk_factor = _risk_factor(rec.risk_level)
    confidence_factor = score_value(profile.confidence_score, dimension="confidence") / 3
    urgency_factor = score_value(profile.actionability_type, dimension="actionability") / 3
    impact_factor = score_value(profile.impact_score, dimension="impact") / 3
    effort_factor = score_value(profile.effort_score, dimension="effort") / 3
    score = computed_priority_score(rec, max_savings)
    return {
        "normalized_savings": round(normalized_savings, 4),
        "risk_factor": round(risk_factor, 4),
        "confidence_factor": round(confidence_factor, 4),
        "urgency_factor": round(urgency_factor, 4),
        "impact_factor": round(impact_factor, 4),
        "effort_factor": round(effort_factor, 4),
        "score": round(score, 4),
    }


def computed_impact_score(rec: Recommendation, max_savings: float = 0.0) -> float:
    return computed_priority_score(rec, max_savings)


def priority_bucket_for(rec_or_score) -> str:
    if isinstance(rec_or_score, Recommendation):
        return priority_bucket_for_recommendation(rec_or_score)
    score = float(rec_or_score or 0)
    if score >= 0.72:
        return "high"
    if score >= 0.48:
        return "medium"
    return "low"


def ranking_reason_for(rec: Recommendation, factors: dict[str, float]) -> str:
    del factors
    return ranking_reason_from_profile(rec)


def rank_by_computed_score(recommendations: list[Recommendation]) -> dict[UUID, int]:
    """1-based rank by computed_impact_score descending."""
    savings_values = [float(r.estimated_savings) for r in recommendations if r.estimated_savings is not None]
    max_savings = max(savings_values) if savings_values else 0.0
    sorted_recs = sorted(recommendations, key=lambda r: recommendation_sort_key(r, max_savings))
    return {rec.id: i + 1 for i, rec in enumerate(sorted_recs)}


def effective_savings_basis(rec: Recommendation) -> str:
    if rec.savings_basis:
        return rec.savings_basis
    return savings_basis_for(rec.recommendation_type, rec.recommendation_category)


def effective_confidence_reason(rec: Recommendation) -> str:
    if rec.confidence_reason:
        return rec.confidence_reason
    return resolved_scoring_profile(rec).confidence_reasoning
