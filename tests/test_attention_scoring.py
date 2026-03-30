"""Unit tests for attention scoring (no database)."""

from datetime import datetime, timedelta, timezone

from app.services.attention_scoring_service import (
    friction_score_from_age,
    score_attention_candidates,
)


def test_friction_buckets():
    now = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
    assert friction_score_from_age(now - timedelta(hours=12), now) == 10.0
    assert friction_score_from_age(now - timedelta(days=2), now) == 30.0
    assert friction_score_from_age(now - timedelta(days=5), now) == 60.0
    assert friction_score_from_age(now - timedelta(days=10), now) == 90.0


def test_failed_sync_outranks_plain_recommendation():
    now = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
    candidates = [
        {
            "kind": "recommendation_top",
            "title": "Cheap win",
            "entity_type": "recommendation",
            "entity_id": "11111111-1111-1111-1111-111111111111",
            "recommendation_id": "11111111-1111-1111-1111-111111111111",
            "estimated_monthly_savings": 500.0,
            "risk_level": "low",
            "preflight_status": None,
            "status": None,
            "execution_eligible": None,
            "blocking_reason": None,
            "error_message": None,
            "execution_notes_excerpt": None,
            "anchor_iso": None,
        },
        {
            "kind": "failed_sync",
            "title": "Failed sync",
            "entity_type": "sync_job",
            "entity_id": "22222222-2222-2222-2222-222222222222",
            "recommendation_id": None,
            "estimated_monthly_savings": None,
            "risk_level": None,
            "preflight_status": None,
            "status": None,
            "execution_eligible": None,
            "blocking_reason": None,
            "error_message": "timeout",
            "execution_notes_excerpt": None,
            "anchor_iso": "2026-03-14T10:00:00+00:00",
        },
    ]
    out = score_attention_candidates(candidates, now=now)
    assert out[0]["item_kind"] == "failed_sync"
    assert out[0]["priority_score"] >= out[1]["priority_score"]
    assert "why_action_needed" in out[0] and "top_reason" in out[0]
    assert out[0]["action_type"] == "fix_failure"


def test_dedupe_keeps_higher_score():
    now = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
    rid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    candidates = [
        {
            "kind": "recommendation_top",
            "title": "Opp A",
            "entity_type": "recommendation",
            "entity_id": rid,
            "recommendation_id": rid,
            "estimated_monthly_savings": 10.0,
            "risk_level": "low",
            "preflight_status": None,
            "status": None,
            "execution_eligible": None,
            "blocking_reason": None,
            "error_message": None,
            "execution_notes_excerpt": None,
            "anchor_iso": None,
        },
        {
            "kind": "execution_blocked",
            "title": "Same rec blocked",
            "entity_type": "recommendation",
            "entity_id": rid,
            "recommendation_id": rid,
            "estimated_monthly_savings": 200.0,
            "risk_level": "high",
            "preflight_status": None,
            "status": None,
            "execution_eligible": False,
            "blocking_reason": "policy",
            "error_message": None,
            "execution_notes_excerpt": None,
            "anchor_iso": "2026-03-10T10:00:00+00:00",
        },
    ]
    out = score_attention_candidates(candidates, now=now)
    assert len(out) == 1
    assert out[0]["item_kind"] == "execution_blocked"
