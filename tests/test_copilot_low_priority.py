"""Defer / low-priority Copilot: two-bucket classification and markdown/structured formatting."""

from types import SimpleNamespace
from uuid import uuid4

from app.services.copilot_low_priority_service import (
    _aggregate,
    classify_deferral_bucket,
    format_deferral_markdown,
    format_deferral_structured,
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


# ── Structured output ─────────────────────────────────────────────────────────

def test_structured_deferral_empty_groups():
    r = format_deferral_structured({}, {})
    assert r.response_type == "low_priority"
    assert r.summary.counts.get("low_priority", 0) == 0
    assert r.summary.counts.get("deferred_important", 0) == 0
    assert r.sections == []


def test_structured_deferral_low_section():
    rec = _rec(savings=2.0, rtype="s3_add_required_tags", summary="Add tags")
    from app.services.copilot_low_priority_service import _Agg

    agg = _Agg(title_display="Add tags")
    agg.add(rec, {"execution_eligible": False}, "low")

    r = format_deferral_structured({"key": agg}, {})
    assert len(r.sections) == 1
    s = r.sections[0]
    assert s.section_type == "low_priority"
    assert len(s.items) == 1
    assert s.items[0]["count"] == 1
    assert s.items[0]["title"] == "Add tags"
    assert r.summary.counts["low_priority"] == 1
    assert r.summary.counts.get("deferred_important", 0) == 0


def test_structured_deferral_deferred_section():
    rec = _rec(savings=25.0, rtype="ec2_rightsizing", category="cost", risk="medium")
    from app.services.copilot_low_priority_service import _Agg

    agg = _Agg(title_display="EC2 rightsizing")
    agg.add(rec, {"execution_eligible": False, "blocking_reason": "approval_required"}, "deferred_important")

    r = format_deferral_structured({}, {"key": agg})
    assert len(r.sections) == 1
    s = r.sections[0]
    assert s.section_type == "deferred_important"
    assert s.items[0]["category"] == "cost"
    assert s.items[0]["status"] == "approval_required"
    assert r.summary.counts["deferred_important"] == 1


def test_structured_deferral_both_sections():
    from app.services.copilot_low_priority_service import _Agg

    rec_low = _rec(savings=2.0, rtype="s3_add_required_tags")
    agg_low = _Agg(title_display="Add tags")
    agg_low.add(rec_low, {"execution_eligible": False}, "low")

    rec_def = _rec(savings=30.0, rtype="ec2_rightsizing", category="cost", risk="high")
    agg_def = _Agg(title_display="EC2 rightsizing")
    agg_def.add(rec_def, {"execution_eligible": False, "blocking_reason": "approval_required"}, "deferred_important")

    r = format_deferral_structured({"low_key": agg_low}, {"def_key": agg_def})
    section_types = {s.section_type for s in r.sections}
    assert "low_priority" in section_types
    assert "deferred_important" in section_types
    assert r.summary.counts["low_priority"] == 1
    assert r.summary.counts["deferred_important"] == 1


def test_structured_meta_fields():
    r = format_deferral_structured({}, {})
    assert r.meta.response_type == "low_priority"
    assert r.meta.version == "1"
    assert len(r.meta.generated_at) > 0
