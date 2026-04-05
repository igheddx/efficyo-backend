"""Defer / low-priority Copilot: two-bucket classification and markdown formatting."""

from types import SimpleNamespace
from uuid import uuid4

from app.services.copilot_low_priority_service import (
    _aggregate,
    classify_deferral_bucket,
    format_deferral_markdown,
)


def _rec(
    *,
    savings: float | None = 3.0,
    summary: str = "Add required cost allocation tags",
    rtype: str = "s3_add_required_tags",
    category: str = "governance",
    risk: str = "low",
):
    return SimpleNamespace(
        id=uuid4(),
        summary=summary,
        recommendation_type=rtype,
        risk_level=risk,
        estimated_savings=savings,
        recommendation_category=category,
    )


def test_low_when_blocked_small_hygiene():
    r = _rec(savings=2.0, rtype="s3_add_required_tags")
    assert classify_deferral_bucket(r, {"execution_eligible": False}) == "low"


def test_low_when_ready_small_hygiene():
    r = _rec(savings=2.0, rtype="s3_add_required_tags")
    assert classify_deferral_bucket(r, {"execution_eligible": True}) == "low"


def test_omit_ready_security_even_if_small_savings():
    r = _rec(savings=2.0, rtype="s3_enable_public_access_block", category="security")
    assert classify_deferral_bucket(r, {"execution_eligible": True}) is None


def test_deferred_important_when_blocked_and_meaningful_savings():
    r = _rec(savings=25.0, rtype="ec2_rightsizing", category="cost")
    assert classify_deferral_bucket(r, {"execution_eligible": False}) == "deferred_important"


def test_deferred_important_when_blocked_security_small_savings():
    r = _rec(savings=2.0, rtype="s3_enable_public_access_block", category="security")
    assert classify_deferral_bucket(r, {"execution_eligible": False}) == "deferred_important"


def test_omit_ready_non_hygiene_small_savings():
    r = _rec(savings=2.0, rtype="ec2_instance_resize", summary="Resize instance for cost")
    assert classify_deferral_bucket(r, {"execution_eligible": True}) is None


def test_dedupe_increments_count_and_max_savings():
    a = _rec(savings=3.0, summary="Same title", rtype="s3_add_required_tags")
    b = _rec(savings=7.0, summary="Same title", rtype="s3_add_required_tags")
    rows = [
        (a, {"execution_eligible": False}, "low"),
        (b, {"execution_eligible": False, "blocking_reason": "approval_required"}, "low"),
    ]
    low_g, def_g = _aggregate(rows)
    assert len(low_g) == 1
    agg = next(iter(low_g.values()))
    assert agg.count == 2
    assert agg.max_savings == 7.0


def test_markdown_sections_and_none():
    text = format_deferral_markdown({}, {})
    assert "LOW PRIORITY (Safe to Defer)" in text
    assert "None" in text
    assert "DEFERRED BUT IMPORTANT" in text
    assert "Summary" in text
    assert "Low Priority Items: 0" in text
