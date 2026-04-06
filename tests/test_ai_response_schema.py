"""Universal AI response schema — validation and roundtrip tests."""

from app.schemas.ai_response import (
    ApprovalSummaryItem,
    DeferredImportantItem,
    GroupedRecommendationItem,
    LowPriorityItem,
    MetricItem,
    NarrativeItem,
    ResponseMeta,
    ResponseSection,
    ResponseSummary,
    StructuredAIResponse,
    TopActionItem,
    TrendItem,
    WarningItem,
    WorkflowSummaryItem,
)


# ── StructuredAIResponse roundtrip ────────────────────────────────────────────

def test_structured_response_roundtrip():
    r = StructuredAIResponse(
        response_type="low_priority",
        title="Test",
        summary=ResponseSummary(counts={"low_priority": 3, "deferred_important": 1}),
        sections=[
            ResponseSection(
                section_type="low_priority",
                title="LOW PRIORITY",
                items=[
                    LowPriorityItem(
                        title="Add required tags",
                        group="Governance / Tagging",
                        count=2,
                        reason="Low impact, safe to defer",
                    ).model_dump()
                ],
            )
        ],
        meta=ResponseMeta(response_type="low_priority"),
    )
    data = r.model_dump()
    assert data["response_type"] == "low_priority"
    assert data["title"] == "Test"
    assert data["summary"]["counts"]["low_priority"] == 3
    assert len(data["sections"]) == 1
    assert data["sections"][0]["section_type"] == "low_priority"
    assert data["sections"][0]["items"][0]["count"] == 2


def test_structured_response_empty_sections():
    r = StructuredAIResponse(
        response_type="general_summary",
        title="Summary",
        summary=ResponseSummary(),
        sections=[],
        meta=ResponseMeta(response_type="general_summary"),
    )
    data = r.model_dump()
    assert data["sections"] == []
    assert data["summary"]["counts"] == {}
    assert data["summary"]["totals"] == {}
    assert data["summary"]["highlights"] == []


# ── Meta ──────────────────────────────────────────────────────────────────────

def test_response_meta_has_generated_at():
    meta = ResponseMeta(response_type="test")
    data = meta.model_dump()
    assert "generated_at" in data
    assert len(data["generated_at"]) > 0
    assert data["version"] == "1"
    assert data["response_type"] == "test"


# ── Item schemas ──────────────────────────────────────────────────────────────

def test_top_action_item_schema():
    item = TopActionItem(
        title="Rightsizing EC2",
        impact="high",
        confidence="medium",
        actionability="review_required",
        why_now="$120/mo savings available",
        action_type="review",
        entity_type="recommendation",
        entity_id="abc-123",
        next_step="Open recommendation detail and set to approved.",
    )
    data = item.model_dump()
    assert data["impact"] == "high"
    assert data["actionability"] == "review_required"
    assert data["entity_id"] == "abc-123"


def test_deferred_important_item_schema():
    item = DeferredImportantItem(
        title="S3 bucket public access enabled",
        category="security",
        group="Security",
        count=2,
        status="approval_required",
        impact="high",
        reason="Security exposure — keep on radar once unblocked",
    )
    data = item.model_dump()
    assert data["category"] == "security"
    assert data["impact"] == "high"
    assert data["count"] == 2


def test_low_priority_item_schema():
    item = LowPriorityItem(
        title="Add cost allocation tags",
        group="Governance / Tagging",
        count=5,
        reason="Low impact, low risk",
    )
    data = item.model_dump()
    assert data["group"] == "Governance / Tagging"
    assert data["count"] == 5


def test_metric_item_schema():
    item = MetricItem(label="Total recommendations", value=42, unit=None)
    data = item.model_dump()
    assert data["value"] == 42
    assert data["label"] == "Total recommendations"


def test_metric_item_with_trend():
    item = MetricItem(label="Spend", value=1500.0, unit="USD", trend="up")
    data = item.model_dump()
    assert data["trend"] == "up"


def test_approval_summary_item_schema():
    item = ApprovalSummaryItem(
        title="Rightsizing approval",
        risk_level="medium",
        estimated_savings=85.0,
        preflight_status="passed",
        approvals_complete=1,
        approvals_required=2,
        entity_id="uuid-001",
        reason="1/2 approvals recorded",
    )
    data = item.model_dump()
    assert data["risk_level"] == "medium"
    assert data["estimated_savings"] == 85.0


def test_workflow_summary_item_status_values():
    for status in ("ready", "blocked", "failed"):
        item = WorkflowSummaryItem(title="Some rec", status=status, reason="test")
        assert item.model_dump()["status"] == status


def test_trend_item_schema():
    item = TrendItem(service="Amazon EC2", direction="up", percent_change=12.5, label="rising")
    data = item.model_dump()
    assert data["direction"] == "up"
    assert data["percent_change"] == 12.5


def test_warning_item_defaults_to_warning_severity():
    item = WarningItem(message="Connection failed")
    assert item.severity == "warning"


def test_narrative_item_schema():
    item = NarrativeItem(text="This is a narrative paragraph.")
    assert item.model_dump()["text"] == "This is a narrative paragraph."


def test_grouped_recommendation_item_schema():
    item = GroupedRecommendationItem(
        title="EC2 Rightsizing",
        group="Top Opportunities",
        count=3,
        impact="high",
        reason="Est. ~$150/mo",
    )
    data = item.model_dump()
    assert data["count"] == 3
    assert data["impact"] == "high"


# ── Section types ─────────────────────────────────────────────────────────────

def test_all_section_types_accepted():
    """Every declared SectionType must be accepted by ResponseSection validation."""
    from typing import get_args
    from app.schemas.ai_response import SectionType

    for section_type in get_args(SectionType):
        section = ResponseSection(section_type=section_type, title="Test", items=[])
        assert section.section_type == section_type


# ── CopilotQueryResponse integration ─────────────────────────────────────────

def test_copilot_query_response_carries_structured():
    from app.schemas.copilot import CopilotQueryResponse

    r = CopilotQueryResponse(
        answer="Test answer",
        structured=StructuredAIResponse(
            response_type="prioritize",
            title="Top Priorities",
            summary=ResponseSummary(counts={"top_actions": 2}),
            sections=[],
            meta=ResponseMeta(response_type="prioritize"),
        ),
    )
    data = r.model_dump()
    assert data["structured"]["response_type"] == "prioritize"
    assert data["structured"]["summary"]["counts"]["top_actions"] == 2


def test_copilot_query_response_structured_defaults_none():
    from app.schemas.copilot import CopilotQueryResponse

    r = CopilotQueryResponse(answer="hello")
    assert r.structured is None
