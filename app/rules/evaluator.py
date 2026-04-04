"""Condition evaluator for config-driven rules.

``evaluate_conditions(conditions, cfg, tags)`` returns True when **all**
conditions in the list match (implicit AND).  Conditions themselves can
test cfg fields, tags_json, computed date values, or multi-field queries.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.rules.registry import RuleCondition


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate_conditions(
    conditions: list[RuleCondition],
    cfg: dict[str, Any],
    tags: dict[str, Any],
) -> bool:
    """Return True when every condition in the list evaluates to True."""
    for cond in conditions:
        if not _eval_one(cond, cfg, tags):
            return False
    return True


# ---------------------------------------------------------------------------
# Internal dispatch
# ---------------------------------------------------------------------------

def _get_field(cfg: dict, field: str | None) -> Any:
    if field is None:
        return None
    return cfg.get(field)


def _coerce_numeric(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_iso_date(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _eval_one(cond: RuleCondition, cfg: dict, tags: dict) -> bool:
    op = (cond.op or "").strip().lower()

    # ------------------------------------------------------------------
    # Tag-based operators
    # ------------------------------------------------------------------
    if op == "any_tag_missing":
        return any(not tags.get(t) for t in cond.tags)

    # ------------------------------------------------------------------
    # Multi-field operators
    # ------------------------------------------------------------------
    if op == "any_bool_true":
        return any(bool(cfg.get(f)) for f in cond.fields)

    if op == "all_bool_false":
        return all(not bool(cfg.get(f)) for f in cond.fields)

    # ------------------------------------------------------------------
    # String operators
    # ------------------------------------------------------------------
    if op == "startswith_any":
        raw_val = _get_field(cfg, cond.field)
        val = str(raw_val or "").strip().lower()
        return bool(val) and any(val.startswith(str(pfx).lower()) for pfx in cond.values)

    if op == "in":
        raw_val = _get_field(cfg, cond.field)
        return raw_val in cond.values

    if op == "not_in":
        raw_val = _get_field(cfg, cond.field)
        return raw_val not in cond.values

    # ------------------------------------------------------------------
    # Date/time operators
    # ------------------------------------------------------------------
    if op == "days_until_lte":
        dt = _parse_iso_date(_get_field(cfg, cond.field))
        if dt is None:
            return False
        days = (dt - datetime.now(timezone.utc)).days
        threshold = _coerce_numeric(cond.value)
        return threshold is not None and days <= threshold

    if op == "days_until_gt":
        dt = _parse_iso_date(_get_field(cfg, cond.field))
        if dt is None:
            return False
        days = (dt - datetime.now(timezone.utc)).days
        threshold = _coerce_numeric(cond.value)
        return threshold is not None and days > threshold

    # ------------------------------------------------------------------
    # Existence operators
    # ------------------------------------------------------------------
    if op == "exists":
        val = _get_field(cfg, cond.field)
        return val is not None

    if op == "missing":
        val = _get_field(cfg, cond.field)
        return val is None

    if op == "not_empty_list":
        val = _get_field(cfg, cond.field)
        return isinstance(val, (list, tuple)) and len(val) > 0

    # ------------------------------------------------------------------
    # Boolean operators
    # ------------------------------------------------------------------
    if op == "bool_true":
        return bool(_get_field(cfg, cond.field))

    if op == "bool_false":
        return not bool(_get_field(cfg, cond.field))

    # ------------------------------------------------------------------
    # Equality / comparison operators
    # ------------------------------------------------------------------
    raw_val = _get_field(cfg, cond.field)

    if op == "eq":
        # Try numeric comparison first; fall back to string
        n_raw = _coerce_numeric(raw_val)
        n_cmp = _coerce_numeric(cond.value)
        if n_raw is not None and n_cmp is not None:
            return n_raw == n_cmp
        return str(raw_val or "") == str(cond.value or "")

    if op == "neq":
        n_raw = _coerce_numeric(raw_val)
        n_cmp = _coerce_numeric(cond.value)
        if n_raw is not None and n_cmp is not None:
            return n_raw != n_cmp
        return str(raw_val or "") != str(cond.value or "")

    if op in ("gt", "gte", "lt", "lte"):
        n_raw = _coerce_numeric(raw_val)
        n_cmp = _coerce_numeric(cond.value)
        if n_raw is None or n_cmp is None:
            return False
        if op == "gt":
            return n_raw > n_cmp
        if op == "gte":
            return n_raw >= n_cmp
        if op == "lt":
            return n_raw < n_cmp
        return n_raw <= n_cmp  # lte

    # Unknown operator – fail safe (no false positive)
    return False
