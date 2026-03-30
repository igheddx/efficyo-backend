"""Deterministic copilot query → intent classification (before LLM)."""

from __future__ import annotations

COPILOT_INTENTS = frozenset(
    {
        "prioritize",
        "blockers",
        "approvals",
        "executions",
        "savings",
        "tenants",
        "low_priority",
        "general_summary",
    }
)


def classify_copilot_intent(query: str) -> str:
    """
    Map natural language to a single operational intent.
    Order: specific phrases before broad defaults.
    """
    q = (query or "").strip().lower()
    if not q:
        return "general_summary"

    # Blockers / stuck / failures
    if any(
        w in q
        for w in (
            "stuck",
            "blocker",
            "blocking",
            "blocked",
            "failing",
            "failure",
            "failed sync",
            "sync fail",
            "execution fail",
            "went wrong",
            "not working",
        )
    ):
        return "blockers"

    # Savings / cost / proof
    if any(
        w in q
        for w in (
            "saving",
            "savings",
            "proof of savings",
            "before and after",
            "cost trend",
            "spend",
            "where are we saving",
            "how much saved",
        )
    ):
        return "savings"

    # Tenants / customers / org-wide
    if any(
        w in q
        for w in (
            "which customer",
            "which tenants",
            "tenant",
            "customers need",
            "customer attention",
            "all accounts",
            "across tenants",
        )
    ):
        return "tenants"

    # Approvals
    if any(
        w in q
        for w in (
            "approve",
            "approval",
            "pending approval",
            "approver",
            "sign off",
            "safe to approve",
        )
    ):
        return "approvals"

    # Executions / run fix
    if any(
        w in q
        for w in (
            "ready to execute",
            "execute",
            "execution",
            "run fix",
            "apply",
            "eligible to run",
        )
    ):
        return "executions"

    # Low / defer / ignore — MUST run before "prioritize" ("low priority" contains "priorit")
    if any(
        phrase in q
        for phrase in (
            "low priority",
            "lowest priority",
            "low-priority",
            "lowest-priority",
            "safely ignore",
            "safe to ignore",
            "safe to defer",
            "can i ignore",
            "ignore for now",
            "not urgent",
            "least important",
            "bottom of the list",
            "back burner",
            "defer for now",
            "can wait",
            "what can wait",
            "put off",
            "only the lowest",
            "lowest priority items",
            "deferred but important",
            "safe to wait",
            "truly low priority",
        )
    ):
        return "low_priority"

    # Prioritize / focus / attention / what to do next (before falling back to generic overview)
    if any(
        w in q
        for w in (
            "priorit",
            "focus",
            "today",
            "what should i",
            "first thing",
            "next step",
            "top priority",
            "most attention",
            "needs attention",
            "need attention",
            "needs the most",
            "what needs",
            "where should i",
            "what matters",
            "biggest impact",
            "biggest issue",
            "highest risk",
            "urgent",
            "critical now",
            "main concern",
        )
    ):
        return "prioritize"

    return "general_summary"


def count_context_payload_items(payload: dict) -> int:
    """Rough count of data points for debug (nested dict/list walk)."""

    def _walk(obj: object) -> int:
        if obj is None:
            return 0
        if isinstance(obj, dict):
            return sum(_walk(v) for v in obj.values())
        if isinstance(obj, (list, tuple)):
            return sum(_walk(x) for x in obj)
        if isinstance(obj, bool):
            return 1
        if isinstance(obj, (int, float, str)):
            return 1
        return 1

    return _walk(payload)
