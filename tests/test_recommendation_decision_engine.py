from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.models.recommendation import Recommendation
from app.services.recommendation_credibility import (
    decision_factors_for,
    priority_bucket_for,
    ranking_reason_for,
)


def _rec(
    *,
    est: Decimal | None,
    risk: str,
    confidence: str,
    category: str,
) -> Recommendation:
    now = datetime.now(timezone.utc)
    return Recommendation(
        id=uuid4(),
        tenant_id=uuid4(),
        cloud_account_id=uuid4(),
        finding_id=uuid4(),
        resource_id="r-1",
        resource_type="s3_bucket",
        recommendation_type="s3_enable_public_access_block",
        recommendation_category=category,
        summary="summary",
        explanation="explanation",
        risk_level=risk,
        confidence_score=confidence,
        recommended_action="action",
        estimated_savings=est,
        created_at=now,
    )


def test_decision_factors_formula_and_priority_bucket():
    rec = _rec(est=Decimal("40"), risk="high", confidence="high", category="security")
    factors = decision_factors_for(rec, max_savings=40.0)
    assert factors["normalized_savings"] == 1.0
    assert factors["risk_factor"] == 1.0
    assert factors["confidence_factor"] == 1.0
    assert factors["urgency_factor"] == 1.0
    assert factors["score"] == 1.0
    assert priority_bucket_for(factors["score"]) == "high"


def test_decision_factors_handles_no_savings():
    rec = _rec(est=None, risk="low", confidence="low", category="governance")
    factors = decision_factors_for(rec, max_savings=0.0)
    assert factors["normalized_savings"] == 0.0
    assert factors["risk_factor"] == 0.3
    assert factors["confidence_factor"] == 0.4
    assert factors["urgency_factor"] == 0.4
    assert factors["score"] == 0.22
    assert priority_bucket_for(factors["score"]) == "low"


def test_ranking_reason_mentions_key_drivers():
    rec = _rec(est=Decimal("25"), risk="high", confidence="medium", category="cost")
    factors = decision_factors_for(rec, max_savings=25.0)
    reason = ranking_reason_for(rec, factors).lower()
    assert "savings" in reason
    assert "confidence" in reason
