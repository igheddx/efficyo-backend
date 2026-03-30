"""Central allowlist for automated safe execution (S3 pilot). v1: narrow types only."""

from __future__ import annotations

from typing import FrozenSet

# Types that may use POST .../execute and may be considered for auto-execution under policy.
SAFE_AUTO_EXECUTION_TYPES: FrozenSet[str] = frozenset({"s3_enable_public_access_block", "s3_add_required_tags"})


def is_safe_auto_execution_type(recommendation_type: str | None) -> bool:
    return (recommendation_type or "").strip().lower() in SAFE_AUTO_EXECUTION_TYPES
