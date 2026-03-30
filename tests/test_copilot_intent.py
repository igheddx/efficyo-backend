"""Unit tests for deterministic copilot intent classification."""

from app.services.copilot_intent_service import classify_copilot_intent


def test_what_needs_attention_maps_to_prioritize():
    assert classify_copilot_intent("What needs the most attention?") == "prioritize"
    assert classify_copilot_intent("what needs attention right now") == "prioritize"


def test_focus_and_today_still_prioritize():
    assert classify_copilot_intent("What should I focus on today?") == "prioritize"


def test_generic_hello_stays_general_summary():
    assert classify_copilot_intent("hello") == "general_summary"


def test_low_priority_phrases_not_misclassified_as_prioritize():
    assert classify_copilot_intent("Return only the lowest priority items") == "low_priority"
    assert classify_copilot_intent("What is safe to defer?") == "low_priority"
    assert classify_copilot_intent("Show low-priority recommendations") == "low_priority"
    assert classify_copilot_intent("Deferred but important vs low priority summary") == "low_priority"
