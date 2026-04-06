from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.ai_response import FallbackAIResponse, StructuredAIResponse  # noqa: F401 – re-exported

CopilotActionType = Literal["execute_now", "approve", "review", "investigate", "fix_failure"]


class CopilotQueryRequest(BaseModel):
    """Client sends current UI context; server enforces access and resolves operational role."""

    query: str = Field(..., min_length=1, max_length=4000)
    organization_id: UUID
    tenant_id: UUID
    cloud_account_id: UUID
    user_role_hint: str | None = Field(
        default=None,
        description="Optional display-only hint from the client; authorization uses server-side grants.",
    )


class CopilotPriorityAction(BaseModel):
    title: str
    reason: str = ""
    next_step: str = Field(
        default="",
        description="One concrete next move tied to this item (not generic UI tour).",
    )
    action_type: CopilotActionType | None = None
    entity_type: Literal["approval_request", "recommendation", "sync_job", "tenant", "none"] | None = None
    entity_id: str | None = None


class CopilotDebugInfo(BaseModel):
    detected_intent: str
    context_item_count: int
    items_sent_to_llm: int | None = None
    grouping_counts: dict[str, int] | None = None


class CopilotQueryResponse(BaseModel):
    answer: str
    priority_actions: list[CopilotPriorityAction] = Field(
        default_factory=list,
        description="Up to 3 ranked priorities after post-processing.",
    )
    insights: list[str] = Field(
        default_factory=list,
        description="Up to 3 specific follow-ups after post-processing.",
    )
    debug: CopilotDebugInfo | None = None
    structured: StructuredAIResponse | None = Field(
        default=None,
        description=(
            "Structured JSON rendering contract. When present the frontend renders "
            "from sections[] instead of parsing the answer text blob."
        ),
    )
    fallback: FallbackAIResponse | None = Field(
        default=None,
        description=(
            "Set when the query cannot be meaningfully answered. "
            "The frontend renders this instead of structured/answer when present."
        ),
    )
