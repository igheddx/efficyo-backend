"""Shape minimal, grouped context for Copilot LLM calls (avoid huge payloads)."""

from __future__ import annotations

from typing import Any

# Items passed to the model (sorted by priority_score DESC): default 5, hard cap 7.
_LLM_ITEM_DEFAULT = 5
_LLM_ITEM_MAX = 7
_HIGH_IMPACT_THRESHOLD = 60.0


def _item_key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("entity_type") or ""), str(row.get("entity_id") or ""))


def _compact_attention_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": row.get("title"),
        "action_type": row.get("action_type"),
        "item_kind": row.get("item_kind"),
        "priority_score": row.get("priority_score"),
        "priority_bucket": row.get("priority_bucket"),
        "impact_score": row.get("impact_score"),
        "why_action_needed": row.get("why_action_needed"),
        "entity_type": row.get("entity_type"),
        "entity_id": row.get("entity_id"),
        "recommendation_id": row.get("recommendation_id"),
    }


def _attention_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    pending = 0
    ready = 0
    failures = 0
    for r in rows:
        k = r.get("item_kind") or ""
        at = r.get("action_type") or ""
        if k in ("approval_pending", "approval_partial"):
            pending += 1
        if k == "execution_ready":
            ready += 1
        if at == "fix_failure":
            failures += 1
    return {
        "pending_approvals": pending,
        "ready_to_execute": ready,
        "failures": failures,
    }


def group_attention_for_llm(rows: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    """
    Assign each row to at most one primary group (priority order in one pass).
    Groups: execute_now, approvals, high_impact, failures.
    """
    groups: dict[str, list[dict[str, Any]]] = {
        "execute_now": [],
        "approvals": [],
        "high_impact": [],
        "failures": [],
    }
    seen: set[tuple[str, str]] = set()

    def _take(gname: str, row: dict[str, Any]) -> None:
        k = _item_key(row)
        if not k[1] or k in seen:
            return
        seen.add(k)
        groups[gname].append(_compact_attention_row(row))

    for row in rows:
        at = str(row.get("action_type") or "")
        imp = float(row.get("impact_score") or 0)
        if at == "execute_now":
            _take("execute_now", row)
        elif at == "fix_failure":
            _take("failures", row)
        elif at == "approve":
            _take("approvals", row)
        elif imp > _HIGH_IMPACT_THRESHOLD and at in ("review", "investigate"):
            _take("high_impact", row)
        elif at == "review":
            _take("high_impact", row)

    grouping_counts = {name: len(items) for name, items in groups.items()}
    return groups, grouping_counts


def build_attention_llm_pack(
    scored_attention: list[dict[str, Any]],
    *,
    intent: str,
    user_question: str,
    scope: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Returns (user_message_payload, debug_meta).
    """
    sorted_rows = sorted(
        scored_attention,
        key=lambda r: float(r.get("priority_score") or 0),
        reverse=True,
    )
    limit = _LLM_ITEM_DEFAULT if intent != "blockers" else _LLM_ITEM_MAX
    limit = min(limit, _LLM_ITEM_MAX, len(sorted_rows))
    top_slice = sorted_rows[:limit]

    counts = _attention_counts(sorted_rows)
    groups, grouping_counts = group_attention_for_llm(top_slice)
    top_items = [_compact_attention_row(r) for r in top_slice]

    payload: dict[str, Any] = {
        "user_question": user_question,
        "detected_intent": intent,
        "groups": groups,
        "top_items": top_items,
        "counts": counts,
        "scope": {
            "effective_operational_access": (scope or {}).get("effective_operational_access"),
            "org_membership_role": (scope or {}).get("org_membership_role"),
            "tenant_name": (scope or {}).get("tenant_name"),
            "cloud_account_name": (scope or {}).get("cloud_account_name"),
        },
    }
    debug = {
        "items_sent_to_llm": len(top_slice),
        "grouping_counts": grouping_counts,
    }
    return payload, debug


def trim_generic_scoped_for_llm(scoped: dict[str, Any], *, intent: str, max_list: int = 5) -> dict[str, Any]:
    """Non-attention intents: shallow copy with long lists truncated."""
    out: dict[str, Any] = {
        "detected_intent_hint": intent,
        "scope": scoped.get("scope"),
        "connection": scoped.get("connection"),
    }
    list_keys = [
        "pending_approvals",
        "ready_to_execute",
        "not_ready_to_execute",
        "top_opportunities",
        "failed_sync_jobs",
        "failed_execution_signals",
        "tenant_directory",
    ]
    for k in list_keys:
        v = scoped.get(k)
        if isinstance(v, list):
            out[k] = v[:max_list]
        elif v is not None:
            out[k] = v
    for k, v in scoped.items():
        if k in out or k in ("scope", "connection", "intent"):
            continue
        if isinstance(v, list):
            out[k] = v[:max_list]
        elif isinstance(v, dict):
            out[k] = v
    return out
