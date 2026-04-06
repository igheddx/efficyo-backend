"""Universal AI response contract — structured JSON for all MEEZI workspace outputs.

Every copilot/workspace AI response is shaped around this contract.  The frontend
renders from ``sections`` — it never parses prose.

Top-level shape
---------------
{
  "response_type": str,          # intent name: prioritize | low_priority | approvals | …
  "title": str,
  "summary": {                   # lightweight counts/totals shown at top
    "counts": {"low_priority": 3, "deferred_important": 1},
    "totals": {"savings_usd": 450.0},
    "highlights": ["string …"]
  },
  "sections": [
    {
      "section_type": str,       # see SectionType
      "title": str,
      "items": [...]             # typed per section_type (see item schemas below)
    }
  ],
  "meta": {
    "generated_at": "ISO-8601",
    "version": "1",
    "response_type": str
  }
}
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

# ── Scalar union literals ──────────────────────────────────────────────────────
ImpactLevel = Literal["low", "medium", "high"]
EffortLevel = Literal["low", "medium", "high"]
ConfidenceLevel = Literal["low", "medium", "high"]
ActionabilityLevel = Literal["auto", "guided", "review_required"]
CategoryType = Literal["security", "cost", "governance", "configuration"]
SectionType = Literal[
    "metrics",
    "grouped_recommendations",
    "grouped_findings",
    "top_actions",
    "low_priority",
    "deferred_important",
    "approvals_summary",
    "workflow_summary",
    "savings_summary",
    "trends_summary",
    "narrative",
    "warnings",
    "resolved_items",
]


# ── Item schemas ───────────────────────────────────────────────────────────────

class MetricItem(BaseModel):
    """A single KPI cell (e.g. total recommendations, rolling spend)."""

    label: str
    value: str | int | float
    unit: str | None = None
    trend: Literal["up", "down", "flat"] | None = None


class TopActionItem(BaseModel):
    """A ranked priority action (section_type=top_actions)."""

    title: str
    count: int | None = None
    impact: ImpactLevel | None = None
    effort: EffortLevel | None = None
    confidence: ConfidenceLevel | None = None
    actionability: ActionabilityLevel | None = None
    why_now: str
    action_type: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    next_step: str | None = None


class GroupedRecommendationItem(BaseModel):
    """One recommendation type/group (section_type=grouped_recommendations)."""

    title: str
    group: str
    count: int
    impact: ImpactLevel | None = None
    effort: EffortLevel | None = None
    confidence: ConfidenceLevel | None = None
    actionability: ActionabilityLevel | None = None
    reason: str


class LowPriorityItem(BaseModel):
    """Safe-to-defer item (section_type=low_priority)."""

    title: str
    group: str  # rec-type label used for visual grouping
    count: int
    reason: str


class DeferredImportantItem(BaseModel):
    """Blocked-but-matters item (section_type=deferred_important)."""

    title: str
    category: CategoryType
    group: str  # human-readable category label
    count: int
    status: str  # "blocked" | "approval_required" | display_label from eligibility
    impact: ImpactLevel | None = None
    reason: str


class ApprovalSummaryItem(BaseModel):
    """Pending approval request (section_type=approvals_summary)."""

    title: str
    risk_level: str | None = None
    estimated_savings: float | None = None
    preflight_status: str | None = None
    approvals_complete: int | None = None
    approvals_required: int | None = None
    entity_id: str | None = None
    reason: str


class WorkflowSummaryItem(BaseModel):
    """Execution-eligibility row (section_type=workflow_summary)."""

    title: str
    status: Literal["ready", "blocked", "failed"]
    entity_type: str | None = None
    entity_id: str | None = None
    blocking_reason: str | None = None
    reason: str


class SavingsSummaryItem(BaseModel):
    """Top savings opportunity (section_type=savings_summary)."""

    label: str
    amount: float | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    reason: str


class TrendItem(BaseModel):
    """Service-level cost trend (section_type=trends_summary)."""

    service: str
    direction: Literal["up", "down", "flat"]
    percent_change: float | None = None
    label: str | None = None


class NarrativeItem(BaseModel):
    """Free-form explanatory paragraph (section_type=narrative)."""

    text: str


class WarningItem(BaseModel):
    """Operational alert or blocking condition (section_type=warnings)."""

    message: str
    severity: Literal["warning", "error", "info"] = "warning"


class ResolvedItem(BaseModel):
    """Completed/resolved work item (section_type=resolved_items)."""

    title: str
    entity_type: str | None = None
    entity_id: str | None = None
    resolved_at: str | None = None
    outcome: str


# ── Section ────────────────────────────────────────────────────────────────────

class ResponseSection(BaseModel):
    """One typed group of items in the response.

    ``items`` holds the raw dicts (model_dump()) of the appropriate item type
    for ``section_type``.  The frontend dispatcher reads ``section_type`` to
    select the correct renderer.
    """

    section_type: SectionType
    title: str
    items: list[Any] = Field(default_factory=list)


# ── Summary bar ────────────────────────────────────────────────────────────────

class ResponseSummary(BaseModel):
    """Lightweight headline counts/totals shown above sections."""

    counts: dict[str, int] = Field(default_factory=dict)
    totals: dict[str, float] = Field(default_factory=dict)
    highlights: list[str] = Field(default_factory=list)


# ── Meta ───────────────────────────────────────────────────────────────────────

class ResponseMeta(BaseModel):
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    version: str = "1"
    response_type: str


# ── Top-level contract ─────────────────────────────────────────────────────────

class StructuredAIResponse(BaseModel):
    """Universal AI response envelope returned in ``CopilotQueryResponse.structured``.

    When this field is present the frontend MUST render from ``sections``.
    The legacy ``answer`` text field is kept for backward-compatibility only
    (e.g. pure-LLM responses that have not yet been migrated).
    """

    response_type: str
    title: str
    summary: ResponseSummary
    sections: list[ResponseSection] = Field(default_factory=list)
    meta: ResponseMeta


# ── Fallback response contract ─────────────────────────────────────────────────

FallbackResponseType = Literal[
    "unsupported",
    "empty_result",
    "clarification_needed",
    "limitation",
]


class FallbackAIResponse(BaseModel):
    """Returned when a query cannot be meaningfully answered.

    The frontend renders ``message`` prominently, ``reason`` as secondary info,
    ``suggestions`` as clickable query starters, and ``supported_areas`` as a
    discovery guide.
    """

    response_type: FallbackResponseType
    message: str
    reason: str
    suggestions: list[str] = Field(default_factory=list)
    supported_areas: list[str] = Field(default_factory=list)
