from app.services.ai_explanation_service import generate_ai_explanation


def test_ai_explanation_contains_context_and_next_step():
    text = generate_ai_explanation(
        recommendation_type="nat_gateway_cost_review",
        resource_id="nat-gateway-cost",
        estimated_savings=12.5,
        risk_level="medium",
        recommended_action="Review NAT traffic and add VPC endpoints where possible.",
        evidence_json={"category": "NAT Gateway", "current_monthly_cost": 32.4},
    )
    assert text is not None
    assert "nat-gateway-cost" in text
    assert "NAT Gateway" in text
    assert "Next step:" in text

