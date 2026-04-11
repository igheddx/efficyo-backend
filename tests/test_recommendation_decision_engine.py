from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.recommendation import Recommendation
from app.services.recommendation_scoring import priority_group_for, recommendation_sort_key, resolved_scoring_profile
from app.services.recommendation_credibility import (
    decision_factors_for,
    ranking_reason_for,
)
from app.services.recommendation_service import recommendation_read_from_orm


def _rec(
    *,
    est: Decimal | None,
    risk: str,
    confidence: str,
    category: str,
    recommendation_type: str = "s3_enable_public_access_block",
    resource_type: str = "s3_bucket",
) -> Recommendation:
    now = datetime.now(timezone.utc)
    return Recommendation(
        id=uuid4(),
        tenant_id=uuid4(),
        cloud_account_id=uuid4(),
        finding_id=uuid4(),
        resource_id="r-1",
        resource_type=resource_type,
        recommendation_type=recommendation_type,
        recommendation_category=category,
        summary="summary",
        explanation="explanation",
        risk_level=risk,
        confidence_score=confidence,
        recommended_action="action",
        estimated_savings=est,
        created_at=now,
    )


def test_resource_aware_profile_for_tagging():
    rec = _rec(
        est=Decimal("0"),
        risk="medium",
        confidence="high",
        category="governance",
        recommendation_type="ec2_add_required_tags",
        resource_type="ec2_instance",
    )
    profile = resolved_scoring_profile(rec)
    assert profile.impact_score == "low"
    assert profile.effort_score == "low"
    assert profile.confidence_score == "high"
    assert profile.actionability_type == "guided"


def test_resource_aware_profile_for_aurora_rightsizing():
    rec = _rec(
        est=Decimal("280"),
        risk="medium",
        confidence="medium",
        category="cost",
        recommendation_type="aurora_serverless_cost_review",
        resource_type="aurora_cluster",
    )
    profile = resolved_scoring_profile(rec)
    assert profile.impact_score == "high"
    assert profile.effort_score == "high"
    assert profile.confidence_score == "medium"
    assert profile.actionability_type == "review_required"


def test_decision_factors_reflect_multi_dimensional_scoring():
    rec = _rec(est=Decimal("40"), risk="high", confidence="high", category="security")
    factors = decision_factors_for(rec, max_savings=40.0)
    assert factors["normalized_savings"] == 1.0
    assert factors["risk_factor"] == 1.0
    assert factors["confidence_factor"] == 1.0
    # s3_enable_public_access_block is now review_required (actionability order=1) → 1/3
    assert factors["urgency_factor"] == pytest.approx(1 / 3, rel=1e-3)
    assert factors["impact_factor"] == 1.0
    assert factors["effort_factor"] == pytest.approx(1 / 3, rel=1e-3)
    assert 0.9 < factors["score"] <= 1.0


def test_priority_order_prefers_quick_win_before_strategic_and_cleanup():
    quick_win = _rec(
        est=Decimal("15"),
        risk="high",
        confidence="high",
        category="security",
        recommendation_type="security_group_restrict_world_open_ports",
        resource_type="security_group",
    )
    strategic = _rec(
        est=Decimal("350"),
        risk="medium",
        confidence="medium",
        category="cost",
        recommendation_type="aurora_serverless_cost_review",
        resource_type="aurora_cluster",
    )
    cleanup = _rec(
        est=None,
        risk="low",
        confidence="high",
        category="governance",
        recommendation_type="ec2_add_required_tags",
        resource_type="ec2_instance",
    )
    ranked = sorted([strategic, cleanup, quick_win], key=recommendation_sort_key)
    assert ranked[0] is quick_win
    assert ranked[1] is strategic
    assert ranked[2] is cleanup
    assert priority_group_for(quick_win) == "quick_win"
    assert priority_group_for(strategic) == "strategic"
    assert priority_group_for(cleanup) == "optional_cleanup"


def test_ranking_reason_mentions_key_drivers():
    rec = _rec(est=Decimal("25"), risk="high", confidence="medium", category="cost")
    factors = decision_factors_for(rec, max_savings=25.0)
    reason = ranking_reason_for(rec, factors).lower()
    assert "impact" in reason or "guided" in reason or "review" in reason
    assert "confidence" in reason


def test_recommendation_read_backfills_new_scoring_fields_for_legacy_rows():
    rec = _rec(
        est=Decimal("8"),
        risk="medium",
        confidence="medium",
        category="cost",
        recommendation_type="lambda_rightsize_memory",
        resource_type="lambda_function",
    )
    rec.impact_score = None
    rec.effort_score = None
    rec.actionability_type = None
    read = recommendation_read_from_orm(rec, rank=1, evidence_json={})
    assert read.impact_score == "medium"
    assert read.effort_score == "low"
    assert read.confidence_score == "medium"
    assert read.actionability_type == "guided"
    assert read.priority_group == "standard"
    assert read.why_this_matters
    assert read.effort_explanation
    assert read.confidence_reasoning
