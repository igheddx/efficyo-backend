from __future__ import annotations


def is_add_required_tags_recommendation(recommendation_type: str | None) -> bool:
    t = (recommendation_type or "").strip().lower()
    return t.endswith("_add_required_tags")
