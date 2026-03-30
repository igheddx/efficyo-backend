"""Copilot LLM payload shaping (grouping, limits)."""

from uuid import uuid4

from app.services.copilot_llm_context import build_attention_llm_pack, group_attention_for_llm


def test_group_assigns_single_bucket_per_entity():
    rows = [
        {
            "item_kind": "execution_ready",
            "action_type": "execute_now",
            "title": "A",
            "entity_type": "recommendation",
            "entity_id": "11111111-1111-1111-1111-111111111111",
            "priority_score": 90,
            "impact_score": 50,
            "why_action_needed": "ready",
        },
        {
            "item_kind": "failed_sync",
            "action_type": "fix_failure",
            "title": "B",
            "entity_type": "sync_job",
            "entity_id": "22222222-2222-2222-2222-222222222222",
            "priority_score": 80,
            "impact_score": 92,
            "why_action_needed": "fail",
        },
    ]
    groups, counts = group_attention_for_llm(rows)
    assert counts["execute_now"] == 1
    assert counts["failures"] == 1
    assert len(groups["execute_now"][0]["title"]) > 0


def test_build_pack_caps_items():
    rows = [
        {
            "item_kind": "recommendation_top",
            "action_type": "review",
            "title": f"T{i}",
            "entity_type": "recommendation",
            "entity_id": str(uuid4()),
            "priority_score": float(100 - i),
            "impact_score": 70.0,
            "why_action_needed": "x",
        }
        for i in range(12)
    ]
    pack, meta = build_attention_llm_pack(rows, intent="prioritize", user_question="q", scope={})
    assert meta["items_sent_to_llm"] <= 7
    assert len(pack["top_items"]) == meta["items_sent_to_llm"]
