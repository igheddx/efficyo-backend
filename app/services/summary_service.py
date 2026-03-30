"""Summary service for cloud optimization insights."""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.cost_window import account_cost_window_fields, round_currency, round_percentage
from app.services import cloud_account_service, cost_explorer_service, recommendation_intelligence_service, recommendation_service


@dataclass
class SummaryResult:
    cloud_account_id: UUID
    total_estimated_monthly_savings: float
    total_cost: float
    savings_percentage: float
    total_recommendations: int
    by_category: dict[str, int]
    by_severity: dict[str, int]
    top_cost_services: list["TopCostService"]
    top_savings_opportunity: "SummaryRecommendation | None"
    top_risk_issue: "SummaryRecommendation | None"
    # Cost Explorer window for total_cost (End is exclusive in the AWS API).
    cost_period_start: str
    cost_period_end: str
    cost_window: str
    cost_window_label: str
    cost_metric: str


@dataclass
class TopCostService:
    service: str
    amount: float


@dataclass
class SummaryRecommendation:
    recommendation_id: UUID
    resource_id: str
    recommendation_type: str
    recommendation_category: str
    summary: str
    estimated_savings: float | None
    risk_level: str
    confidence_score: str
    learned_confidence: str | None = None
    learned_confidence_reason: str | None = None
    historical_success_rate: float | None = None
    avg_realized_savings_for_type: float | None = None


def _risk_priority(risk_level: str) -> int:
    if risk_level == "high":
        return 3
    if risk_level == "medium":
        return 2
    if risk_level == "low":
        return 1
    return 0


def _to_summary_recommendation(
    recommendation,
    stats_by_type: dict | None = None,
) -> SummaryRecommendation:
    intel = (
        recommendation_intelligence_service.learned_intelligence_for_recommendation(recommendation, stats_by_type or {})
        if stats_by_type is not None
        else {
            "learned_confidence": recommendation.confidence_score,
            "learned_confidence_reason": None,
            "historical_success_rate": None,
            "avg_realized_savings_for_type": None,
        }
    )
    return SummaryRecommendation(
        recommendation_id=recommendation.id,
        resource_id=recommendation.resource_id,
        recommendation_type=recommendation.recommendation_type,
        recommendation_category=recommendation.recommendation_category,
        summary=recommendation.summary,
        estimated_savings=(
            round_currency(recommendation.estimated_savings)
            if recommendation.estimated_savings is not None
            else None
        ),
        risk_level=recommendation.risk_level,
        confidence_score=recommendation.confidence_score,
        learned_confidence=intel.get("learned_confidence"),
        learned_confidence_reason=intel.get("learned_confidence_reason"),
        historical_success_rate=intel.get("historical_success_rate"),
        avg_realized_savings_for_type=intel.get("avg_realized_savings_for_type"),
    )


def get_cloud_account_summary(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> SummaryResult:
    """Build summary from latest recommendations for a tenant-scoped cloud account."""
    cloud_account = cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)

    cost_summary = cost_explorer_service.fetch_cost_summary(role_arn=cloud_account.role_arn)
    _cw_defaults = account_cost_window_fields()
    cost_window = str(cost_summary.get("cost_window") or _cw_defaults["cost_window"])
    cost_window_label = str(cost_summary.get("cost_window_label") or _cw_defaults["cost_window_label"])
    cost_metric = str(cost_summary.get("cost_metric") or _cw_defaults["cost_metric"])
    total_cost_decimal = Decimal(str(cost_summary.get("total_cost", 0.0)))
    top_cost_services = [
        TopCostService(service=item["service"], amount=round_currency(item["amount"]))
        for item in cost_summary.get("by_service", [])[:3]
    ]

    recommendations = recommendation_service.list_recommendations(
        db_session,
        tenant_id,
        cloud_account_id,
        latest_only=True,
    )

    stats_by_type = recommendation_intelligence_service.get_recommendation_type_stats(
        db_session, tenant_id, cloud_account_id
    )

    total_estimated_savings = Decimal("0.00")
    by_category = {"cost": 0, "security": 0, "governance": 0}
    by_severity = {"high": 0, "medium": 0, "low": 0}
    top_savings_recommendation = None
    top_savings_value: Decimal | None = None
    top_risk_recommendation = None
    top_risk_priority = -1

    for recommendation in recommendations:
        by_category.setdefault(recommendation.recommendation_category, 0)
        by_category[recommendation.recommendation_category] += 1

        by_severity.setdefault(recommendation.risk_level, 0)
        by_severity[recommendation.risk_level] += 1

        current_risk_priority = _risk_priority(recommendation.risk_level)
        if current_risk_priority > top_risk_priority:
            top_risk_priority = current_risk_priority
            top_risk_recommendation = recommendation

        if recommendation.estimated_savings is not None:
            savings = Decimal(str(recommendation.estimated_savings))
            total_estimated_savings += savings

            if top_savings_value is None or savings > top_savings_value:
                top_savings_value = savings
                top_savings_recommendation = recommendation

    savings_percentage = Decimal("0.00")
    if total_cost_decimal > Decimal("0.00"):
        savings_percentage = (total_estimated_savings / total_cost_decimal) * Decimal("100")

    return SummaryResult(
        cloud_account_id=cloud_account_id,
        total_estimated_monthly_savings=round_currency(total_estimated_savings),
        total_cost=round_currency(total_cost_decimal),
        savings_percentage=round_percentage(savings_percentage),
        total_recommendations=len(recommendations),
        by_category=by_category,
        by_severity=by_severity,
        top_cost_services=top_cost_services,
        cost_period_start=str(cost_summary.get("start_date", "")),
        cost_period_end=str(cost_summary.get("end_date", "")),
        cost_window=cost_window,
        cost_window_label=cost_window_label,
        cost_metric=cost_metric,
        top_savings_opportunity=(
            _to_summary_recommendation(top_savings_recommendation, stats_by_type)
            if top_savings_recommendation is not None
            else None
        ),
        top_risk_issue=(
            _to_summary_recommendation(top_risk_recommendation, stats_by_type)
            if top_risk_recommendation is not None
            else None
        ),
    )
