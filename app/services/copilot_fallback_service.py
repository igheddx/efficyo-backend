"""Fallback response builder for MEEZI copilot.

Classifies queries that cannot be meaningfully answered into one of four
categories and returns a structured, guided response instead of a blob or
silent failure.

Detection flow
--------------
classify_query_feasibility(query, intent, scoped) → feasibility status
  • "unsupported"       → clearly outside MEEZI domain (weather, coding, etc.)
  • "ambiguous"         → too short or generic to act on
  • "no_data"           → supported intent but no data in scoped context
  • "system_limitation" → intent recognised but cannot be fulfilled right now
  • "supported"         → proceed with normal response pipeline

build_fallback_response(status, query, intent) → FallbackAIResponse
"""

from __future__ import annotations

from typing import Any, Literal

from app.schemas.ai_response import FallbackAIResponse

# ── Supported query suggestions ────────────────────────────────────────────────

_STANDARD_SUGGESTIONS: list[str] = [
    "Show top actions",
    "Show cost savings opportunities",
    "Show high risk issues",
    "Show low priority items",
    "Show recommendations",
]

_INTENT_SUGGESTIONS: dict[str, list[str]] = {
    "prioritize": [
        "What should I focus on today?",
        "Show top actions",
        "Show high risk issues",
        "What needs attention?",
        "Show recommendations",
    ],
    "blockers": [
        "What is stuck or failing?",
        "Show blocking issues",
        "Show sync failures",
        "Show execution blockers",
        "Show high risk issues",
    ],
    "approvals": [
        "Show pending approvals",
        "What needs sign-off?",
        "Show high risk approvals",
        "Show top actions",
        "Show recommendations",
    ],
    "executions": [
        "What can I run now?",
        "Show ready to execute items",
        "Show blocked executions",
        "Show top actions",
        "Show recommendations",
    ],
    "savings": [
        "Show cost savings opportunities",
        "Show savings proof",
        "Show cost trends",
        "Show top opportunities",
        "Show recommendations",
    ],
    "tenants": [
        "Which tenants need attention?",
        "Show tenant overview",
        "Show connection issues",
        "Show top actions",
        "Show recommendations",
    ],
    "general_summary": _STANDARD_SUGGESTIONS,
    "low_priority": [
        "Show low priority items",
        "What can I safely defer?",
        "Show deferred items",
        "Show recommendations",
        "Show top actions",
    ],
}

_SUPPORTED_AREAS: list[str] = [
    "Top priority actions and what to focus on",
    "Blocked or failing items needing attention",
    "Pending approvals and sign-off queue",
    "Execution-ready and blocked recommendations",
    "Cost savings opportunities and realized proof",
    "Tenant and account health overview",
    "Low priority and safely deferred items",
]

# ── Off-domain keyword patterns ────────────────────────────────────────────────
# Any query containing one of these phrases is classified as "unsupported".

_OFF_DOMAIN_PHRASES: tuple[str, ...] = (
    # Coding / scripting
    "write code",
    "write a script",
    "write a function",
    "write sql",
    "give me code",
    "code for",
    "python script",
    "bash script",
    "shell script",
    "terraform",
    "create a pipeline",
    # General knowledge
    "weather",
    "news today",
    "translate ",
    "what is the capital",
    "history of",
    "who invented",
    "meaning of",
    # General AI filler tasks
    "tell me a joke",
    "write a poem",
    "write an email",
    "write a letter",
    "write a report",
    "explain the concept",
    # Unrelated tech operations
    "kubernetes",
    "docker run",
    "docker build",
    "ci/cd",
    "github actions",
    "jira ticket",
    "create a ticket",
    "send an email",
    "send a message",
    "send a slack",
    # Math / general
    "convert currency",
    "what time is it",
    "calculate ",
)

# Queries shorter than this many words are classified as "ambiguous".
_AMBIGUOUS_MIN_WORDS = 2

# Exact single-token queries that are ambiguous regardless of word count.
_AMBIGUOUS_EXACT: frozenset[str] = frozenset(
    {"hi", "hello", "hey", "help", "test", "?", "ok", "yes", "no", "what", "how", "why"}
)


# ── Detection helpers ──────────────────────────────────────────────────────────


def _is_unsupported(query: str) -> bool:
    ql = query.lower()
    return any(phrase in ql for phrase in _OFF_DOMAIN_PHRASES)


def _is_ambiguous(query: str) -> bool:
    stripped = query.strip()
    if stripped.lower() in _AMBIGUOUS_EXACT:
        return True
    words = stripped.split()
    return len(words) < _AMBIGUOUS_MIN_WORDS


def _is_empty_result(intent: str, scoped: dict[str, Any]) -> bool:
    """Return True when the scoped context has no meaningful data for this intent."""
    if not scoped:
        return True

    if intent in ("prioritize", "blockers"):
        scored = scoped.get("scored_attention_top") or []
        pending = scoped.get("pending_approvals") or []
        ready = scoped.get("ready_to_execute") or []
        failures = scoped.get("sync_failures") or []
        return (
            len(scored) == 0
            and len(pending) == 0
            and len(ready) == 0
            and len(failures) == 0
        )

    if intent == "approvals":
        return len(scoped.get("pending_approvals") or []) == 0

    if intent == "executions":
        return (
            len(scoped.get("ready_to_execute") or []) == 0
            and len(scoped.get("not_ready_to_execute") or []) == 0
        )

    if intent == "savings":
        acc = scoped.get("savings_proof_account") or {}
        ten = scoped.get("savings_proof_tenant") or {}
        tops = scoped.get("top_opportunities") or []
        return (
            acc.get("total_estimated_monthly_savings_proof") is None
            and ten.get("total_estimated_monthly_savings_proof") is None
            and len(tops) == 0
        )

    if intent == "tenants":
        return len(scoped.get("tenant_directory") or []) == 0

    if intent == "general_summary":
        summ = scoped.get("summary") or {}
        if isinstance(summ, dict) and summ.get("error"):
            return True
        return not summ

    # low_priority handles its own empty path — treat as supported here
    return False


# ── Public API ─────────────────────────────────────────────────────────────────

FeasibilityStatus = Literal["supported", "unsupported", "no_data", "ambiguous", "system_limitation"]


def classify_query_feasibility(
    query: str,
    intent: str,
    scoped: dict[str, Any],
) -> FeasibilityStatus:
    """Classify whether the query can be meaningfully answered.

    Returns one of:
      "supported"          – proceed with normal response pipeline
      "unsupported"        – clearly outside the MEEZI domain
      "no_data"            – supported intent but context carries no data
      "ambiguous"          – query too short or generic to act on
      "system_limitation"  – intent recognised but cannot be fulfilled now
    """
    if _is_unsupported(query):
        return "unsupported"
    if _is_ambiguous(query):
        return "ambiguous"
    if _is_empty_result(intent, scoped):
        return "no_data"
    return "supported"


# ── Response builder ───────────────────────────────────────────────────────────

_RESPONSE_TYPE_MAP: dict[str, str] = {
    "unsupported": "unsupported",
    "no_data": "empty_result",
    "ambiguous": "clarification_needed",
    "system_limitation": "limitation",
}

_MESSAGE_MAP: dict[str, str] = {
    "unsupported": "This question is outside MEEZI's supported areas.",
    "no_data": "No data is available to answer this right now.",
    "ambiguous": "Your question needs more detail to give a useful answer.",
    "system_limitation": "This request cannot be fulfilled due to a system limitation.",
}

_REASON_MAP: dict[str, str] = {
    "unsupported": (
        "MEEZI focuses on AWS cloud governance, cost optimisation, and operational "
        "workflows. It cannot answer general knowledge, coding, or unrelated tech questions."
    ),
    "no_data": (
        "The account's data may be empty, still syncing, or outside the current scope. "
        "Try re-syncing the account, switching cloud account context, or asking about a "
        "different area such as cost savings or low priority items."
    ),
    "ambiguous": (
        "The query is too short or generic for MEEZI to determine which operational area "
        "you are asking about. Try one of the suggestions below."
    ),
    "system_limitation": (
        "The requested operation is recognised but cannot be completed in the current "
        "session context. Contact support if this persists."
    ),
}


def build_fallback_response(
    status: str,
    query: str,
    intent: str,
) -> FallbackAIResponse:
    """Build a structured fallback response for a non-supported query."""
    rt = _RESPONSE_TYPE_MAP.get(status, "unsupported")  # type: ignore[assignment]
    message = _MESSAGE_MAP.get(status, "This query cannot be answered right now.")
    reason = _REASON_MAP.get(status, "Unknown limitation.")

    # Suggestions are tailored per status + intent
    if status == "no_data":
        # Point toward other areas that are likely to have data
        suggestions = [
            "Show low priority items",
            "Show cost savings opportunities",
            "Show recommendations",
            "Show top actions",
            "Show general account summary",
        ]
    elif status == "ambiguous":
        suggestions = list(_INTENT_SUGGESTIONS.get(intent, _STANDARD_SUGGESTIONS))
    else:
        suggestions = list(_STANDARD_SUGGESTIONS)

    return FallbackAIResponse(
        response_type=rt,  # type: ignore[arg-type]
        message=message,
        reason=reason,
        suggestions=suggestions[:5],
        supported_areas=list(_SUPPORTED_AREAS),
    )
