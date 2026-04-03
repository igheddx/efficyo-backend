from app.services.finding_templates import build_finding_evidence


def test_build_finding_evidence_supports_optional_impact_fields():
    payload = build_finding_evidence(
        title="Example",
        summary="Example summary",
        category="security",
        risk="medium",
        confidence="high",
        recommendation_seed="example_seed",
        approval_required=True,
        execution_eligible=False,
        evidence={"a": 1},
        impact_summary="Improves reliability",
        savings_estimate=12.5,
        risk_explanation="May lead to service instability",
        linked_resources=[{"resource_type": "target_group", "resource_id": "tg-1"}],
    )

    assert payload["impact_summary"] == "Improves reliability"
    assert payload["savings_estimate"] == 12.5
    assert payload["risk_explanation"] == "May lead to service instability"
    assert payload["linked_resources"][0]["resource_type"] == "target_group"
