"""Operations Copilot: intent-scoped context + optional LLM; never executes changes."""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.schemas.ai_response import (
    ApprovalSummaryItem,
    GroupedRecommendationItem,
    MetricItem,
    NarrativeItem,
    ResponseMeta,
    ResponseSection,
    ResponseSummary,
    SavingsSummaryItem,
    StructuredAIResponse,
    TopActionItem,
    TrendItem,
    WarningItem,
    WorkflowSummaryItem,
)
from app.schemas.copilot import CopilotDebugInfo, CopilotPriorityAction, CopilotQueryResponse
from app.services.copilot_context_service import run_intent_and_scoped_context
from app.services.copilot_fallback_service import build_fallback_response, classify_query_feasibility
from app.services.copilot_intent_service import count_context_payload_items
from app.services.copilot_low_priority_service import build_low_priority_copilot_answer
from app.services.copilot_llm_context import (
    build_attention_llm_pack,
    trim_generic_scoped_for_llm,
)

logger = logging.getLogger(__name__)

# ── Structured-response helpers ────────────────────────────────────────────────

def _score_to_impact(score: float) -> str | None:
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def _risk_to_impact(risk: Any) -> str | None:
    r = (risk or "").strip().lower()
    return r if r in ("high", "medium", "low") else None


def _at_to_actionability(action_type: Any) -> str | None:
    at = str(action_type or "").strip().lower()
    if at == "execute_now":
        return "auto"
    if at in ("review", "investigate"):
        return "guided"
    if at in ("approve", "fix_failure"):
        return "review_required"
    return None


# ── Final response caps after post-processing ─────────────────────────────────
_MAX_COPILOT_PRIORITIES = 3
_MAX_INSIGHTS_FINAL = 3

_NEXT_STEP_BY_TYPE: dict[str, str] = {
    "execute_now": "Run the approved change from the recommendation detail when your window allows and eligibility stays green.",
    "approve": "Open this approval request and record approve or reject with a one-line rationale.",
    "review": "Decide whether this savings item should move to approval, using the stated risk and impact only.",
    "investigate": "Read the specific block (eligibility, execution notes, or error text) and clear that gate before moving on.",
    "fix_failure": "Fix the AWS, permission, or connectivity root cause, then retry the failed sync or execution from Advanced.",
}

_GENERIC_INSIGHT_RES = [
    re.compile(r"open\s+recommendations?", re.I),
    re.compile(r"compare\s+impact\s+(vs\.?|versus)\s+risk", re.I),
    re.compile(r"go\s+to\s+approvals?", re.I),
    re.compile(r"click\s+(a\s+)?priority", re.I),
    re.compile(r"use\s+the\s+linked\s+items", re.I),
    re.compile(r"route\s+high-impact", re.I),
]

_SYSTEM_PROMPT = """You are a senior MSP operator helping a teammate on fptNext (multi-tenant AWS cost & governance). Write like a concise human, not a robot or status dashboard.

Input shape (prioritize/blockers):
- user_question, detected_intent
- groups: { execute_now, approvals, high_impact, failures } — each is a short list of items with action_type, title, priority_score, impact_score, why_action_needed, entity ids
- top_items: the same items in priority order (small list, already capped)
- counts: pending_approvals, ready_to_execute, failures
- scope: tenant/account names and effective_operational_access only

Other intents: payload uses scoped_data (trimmed lists/dicts) instead of groups — same JSON output contract.

Rules:
- Do NOT recap totals (recommendation counts, org-wide spend) unless detected_intent is general_summary or the user explicitly asked for an overview.
- Order your thinking: (1) execute_now items first, (2) high_impact reviews, (3) failures / blocked work, (4) approvals queue — unless the user question clearly demands a different order.
- Use each item's action_type to choose verbs: execute_now → run/apply; approve → review/sign-off; review → size up; investigate → diagnose blockers; fix_failure → repair pipeline.
- Speak only from fields present. No invented IDs, ARNs, dollar amounts not in the payload, or shell/AWS CLI.

Output a single JSON object (no markdown) with keys:
- answer: 2–4 short sentences, direct and conversational (e.g. how you'd answer "where should I act first?").
- priority_actions: length 0–3. Each: {title, reason, next_step, action_type, entity_type, entity_id}. reason = why this item matters (one tight sentence). next_step = one specific move for THIS item (not "open recommendations" or generic tours). action_type must be one of: execute_now, approve, review, investigate, fix_failure.
- insights: 0–3 strings, each a distinct tactical point that is NOT generic UI navigation (forbid "open recommendations", "compare impact vs risk", "go to approvals" as standalone advice).

entity_type: approval_request | recommendation | sync_job | tenant | none. entity_id: UUID string when known.

If effective_operational_access is viewer, say they need an approver/admin to execute or approve — do not imply they can run fixes."""


def _entity_type_ok(v: object) -> str | None:
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("approval_request", "recommendation", "sync_job", "tenant", "none"):
        return s
    return None


def _action_type_ok(v: object) -> str | None:
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("execute_now", "approve", "review", "investigate", "fix_failure"):
        return s
    return None


def _insight_is_generic(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 14:
        return True
    return any(p.search(t) for p in _GENERIC_INSIGHT_RES)


def _infer_action_type_from_action(a: CopilotPriorityAction) -> str:
    fixed = _action_type_ok(a.action_type)
    if fixed:
        return fixed
    et = (a.entity_type or "").lower()
    if et == "approval_request":
        return "approve"
    if et == "sync_job":
        return "fix_failure"
    if et == "recommendation":
        r = (a.reason or "").lower()
        if "not eligible" in r or "blocking" in r or "blocked" in r:
            return "investigate"
        if "eligible" in r and ("run" in r or "apply" in r or "pending application" in r):
            return "execute_now"
        return "review"
    if et == "tenant":
        return "investigate"
    return "investigate"


def _dedupe_priority_actions(actions: list[CopilotPriorityAction]) -> list[CopilotPriorityAction]:
    seen_ids: set[str] = set()
    seen_titles: set[str] = set()
    out: list[CopilotPriorityAction] = []
    for a in actions:
        eid_key = f"{a.entity_type or ''}:{a.entity_id or ''}"
        tkey = (a.title or "").strip().lower()[:220]
        if a.entity_id and eid_key in seen_ids:
            continue
        if tkey and tkey in seen_titles:
            continue
        if a.entity_id:
            seen_ids.add(eid_key)
        if tkey:
            seen_titles.add(tkey)
        out.append(a)
    return out


def _fill_next_steps(actions: list[CopilotPriorityAction]) -> list[CopilotPriorityAction]:
    filled: list[CopilotPriorityAction] = []
    for a in actions:
        at = _action_type_ok(a.action_type) or _infer_action_type_from_action(a)
        ns = (a.next_step or "").strip() or _NEXT_STEP_BY_TYPE.get(at, _NEXT_STEP_BY_TYPE["investigate"])
        filled.append(a.model_copy(update={"next_step": ns[:1000], "action_type": at}))
    return filled


def _postprocess_copilot_response(resp: CopilotQueryResponse) -> CopilotQueryResponse:
    acts = _dedupe_priority_actions(list(resp.priority_actions))
    acts = _fill_next_steps(acts)[:_MAX_COPILOT_PRIORITIES]
    insights_raw = [str(x).strip() for x in resp.insights if str(x).strip()]
    insights_f: list[str] = []
    seen_ins: set[str] = set()
    for line in insights_raw:
        if _insight_is_generic(line):
            continue
        lk = line.lower()[:200]
        if lk in seen_ins:
            continue
        seen_ins.add(lk)
        insights_f.append(line[:1000])
    insights_f = insights_f[:_MAX_INSIGHTS_FINAL]
    if len(insights_f) < _MAX_INSIGHTS_FINAL and acts:
        for a in acts:
            if len(insights_f) >= _MAX_INSIGHTS_FINAL:
                break
            step = (a.next_step or "").strip()
            if step and not _insight_is_generic(step) and step.lower() not in seen_ins:
                seen_ins.add(step.lower()[:200])
                insights_f.append(step)
    return resp.model_copy(update={"priority_actions": acts, "insights": insights_f})


def _llm_response_is_weak(resp: CopilotQueryResponse | None) -> bool:
    if resp is None:
        return True
    if len((resp.answer or "").strip()) < 14:
        return True
    if not resp.priority_actions:
        return True
    return False


def _count_trimmed_scoped_items(trimmed: dict[str, Any]) -> int:
    n = 0
    for v in trimmed.values():
        if isinstance(v, list):
            n += len(v)
        elif isinstance(v, dict):
            n += 1
    return max(1, n)


def _fallback_attention_response(
    scored: list[dict[str, Any]],
    *,
    intent: str,
    effective_access: str,
) -> CopilotQueryResponse:
    """Deterministic copy when LLM is off or returns unusable output."""
    if not scored:
        return CopilotQueryResponse(
            answer="Nothing ranked for this account right now — if you expected work here, re-check sync and AWS connectivity.",
            priority_actions=[],
            insights=[],
        )
    actions: list[CopilotPriorityAction] = []
    for row in scored[:_MAX_COPILOT_PRIORITIES]:
        at = _action_type_ok(row.get("action_type")) or "investigate"
        et = _entity_type_ok(row.get("entity_type"))
        eid = row.get("entity_id")
        ns = _NEXT_STEP_BY_TYPE.get(at, _NEXT_STEP_BY_TYPE["investigate"])
        actions.append(
            CopilotPriorityAction(
                title=str(row.get("title") or "Item")[:500],
                reason=str(row.get("why_action_needed") or "")[:2000],
                next_step=ns,
                action_type=at,
                entity_type=et,
                entity_id=str(eid) if eid else None,
            )
        )
    has_x = any(r.get("action_type") == "execute_now" for r in scored[:5])
    has_f = any(r.get("action_type") == "fix_failure" for r in scored[:5])
    has_h = any(float(r.get("impact_score") or 0) > 60 for r in scored[:5])
    bits = []
    if has_x:
        bits.append("run what’s already approved and eligible")
    if has_h:
        bits.append("pull the biggest savings items into review")
    if has_f:
        bits.append("fix failing sync or execution before trusting new approvals")
    if not bits:
        bits.append("work the ranked list top-down")
    lead = f"Start by {'; then '.join(bits)}."
    if intent == "blockers":
        lead = "Unblock failures and stuck gates first — then reopen the normal execution path."
    if (effective_access or "").lower() == "viewer":
        lead += " (Your access is viewer — pair with an approver for sign-off and execution.)"
    insights = [a.next_step for a in actions if a.next_step][: _MAX_INSIGHTS_FINAL]
    return CopilotQueryResponse(answer=lead[:8000], priority_actions=actions, insights=insights)


def _fallback_attention_structured(
    scored: list[dict[str, Any]],
    *,
    intent: str,
) -> StructuredAIResponse:
    """Build a StructuredAIResponse for the attention fallback (prioritize/blockers)."""
    items = [
        TopActionItem(
            title=str(row.get("title") or "")[:200],
            impact=_score_to_impact(float(row.get("impact_score") or 0)),  # type: ignore[arg-type]
            confidence="medium",
            actionability=_at_to_actionability(row.get("action_type")),  # type: ignore[arg-type]
            why_now=str(row.get("why_action_needed") or "")[:500],
            action_type=_action_type_ok(row.get("action_type")),
            entity_type=_entity_type_ok(row.get("entity_type")),
            entity_id=str(row.get("entity_id")) if row.get("entity_id") else None,
            next_step=_NEXT_STEP_BY_TYPE.get(
                _action_type_ok(row.get("action_type")) or "investigate",
                _NEXT_STEP_BY_TYPE["investigate"],
            ),
        )
        for row in scored[:_MAX_COPILOT_PRIORITIES]
    ]
    title = "Top Priorities" if intent == "prioritize" else "Active Blockers"
    return StructuredAIResponse(
        response_type=intent,
        title=title,
        summary=ResponseSummary(counts={"top_actions": len(items)}),
        sections=[
            ResponseSection(
                section_type="top_actions",
                title=title,
                items=[i.model_dump() for i in items],
            )
        ] if items else [],
        meta=ResponseMeta(response_type=intent),
    )


def _rules_based_structured(
    intent: str,
    scoped: dict[str, Any],
) -> StructuredAIResponse:
    """Build a StructuredAIResponse from rules-based context data."""
    sections: list[ResponseSection] = []
    summary_counts: dict[str, int] = {}

    if intent in ("prioritize", "blockers"):
        scored = scoped.get("scored_attention_top") or []
        items = [
            TopActionItem(
                title=str(row.get("title") or "")[:200],
                impact=_score_to_impact(float(row.get("impact_score") or 0)),  # type: ignore[arg-type]
                confidence="medium",
                actionability=_at_to_actionability(row.get("action_type")),  # type: ignore[arg-type]
                why_now=str(row.get("why_action_needed") or "")[:500],
                action_type=_action_type_ok(row.get("action_type")),
                entity_type=_entity_type_ok(row.get("entity_type")),
                entity_id=str(row.get("entity_id")) if row.get("entity_id") else None,
                next_step=_NEXT_STEP_BY_TYPE.get(
                    _action_type_ok(row.get("action_type")) or "investigate",
                    _NEXT_STEP_BY_TYPE["investigate"],
                ),
            )
            for row in scored[:_MAX_COPILOT_PRIORITIES]
        ]
        if items:
            label = "Top Priorities" if intent == "prioritize" else "Active Blockers"
            sections.append(
                ResponseSection(
                    section_type="top_actions",
                    title=label,
                    items=[i.model_dump() for i in items],
                )
            )
        summary_counts["top_actions"] = len(items)

    elif intent == "approvals":
        pending = scoped.get("pending_approvals") or []
        ap_items = [
            ApprovalSummaryItem(
                title=(pa.get("summary") or "Pending approval")[:100],
                risk_level=pa.get("risk_level"),
                estimated_savings=float(pa["estimated_savings"]) if pa.get("estimated_savings") is not None else None,
                preflight_status=pa.get("preflight_status"),
                approvals_complete=pa.get("approvals_complete"),
                approvals_required=pa.get("approvals_required"),
                entity_id=pa.get("approval_request_id"),
                reason=(
                    f"{pa.get('approvals_complete', 0)}/{pa.get('approvals_required', 0)} approvals recorded"
                ),
            )
            for pa in pending[:5]
        ]
        if ap_items:
            sections.append(
                ResponseSection(
                    section_type="approvals_summary",
                    title="Pending Approvals",
                    items=[i.model_dump() for i in ap_items],
                )
            )
        summary_counts["pending_approvals"] = len(ap_items)

    elif intent == "executions":
        ready = scoped.get("ready_to_execute") or []
        not_ready = scoped.get("not_ready_to_execute") or []
        wf_items = []
        for r in (list(ready) + list(not_ready))[:5]:
            ee = r.get("execution_eligibility") or {}
            elig = bool(ee.get("execution_eligible"))
            wf_items.append(
                WorkflowSummaryItem(
                    title=(r.get("summary") or r.get("recommendation_type") or "Recommendation")[:90],
                    status="ready" if elig else "blocked",
                    entity_type="recommendation",
                    entity_id=str(r["recommendation_id"]) if r.get("recommendation_id") else None,
                    blocking_reason=(ee.get("blocking_reason") or None) if not elig else None,
                    reason=(ee.get("display_label") or "Eligible") if elig else (ee.get("display_label") or ee.get("blocking_reason") or "Not eligible"),
                )
            )
        if wf_items:
            sections.append(
                ResponseSection(
                    section_type="workflow_summary",
                    title="Execution Status",
                    items=[i.model_dump() for i in wf_items],
                )
            )
        summary_counts["ready"] = len(ready)
        summary_counts["blocked"] = len(not_ready)

    elif intent == "savings":
        acc = scoped.get("savings_proof_account") or {}
        ten = scoped.get("savings_proof_tenant") or {}
        metric_items = []
        if acc.get("total_estimated_monthly_savings_proof") is not None:
            metric_items.append(
                MetricItem(
                    label="Account savings proof (30d)",
                    value=float(acc["total_estimated_monthly_savings_proof"]),
                    unit="USD/mo",
                )
            )
        if ten.get("total_estimated_monthly_savings_proof") is not None:
            metric_items.append(
                MetricItem(
                    label="Tenant savings proof (30d)",
                    value=float(ten["total_estimated_monthly_savings_proof"]),
                    unit="USD/mo",
                )
            )
        if metric_items:
            sections.append(
                ResponseSection(
                    section_type="metrics",
                    title="Savings Proof",
                    items=[i.model_dump() for i in metric_items],
                )
            )
        tops = scoped.get("top_opportunities") or []
        if not tops:
            summ = scoped.get("summary") or {}
            if isinstance(summ, dict):
                tso = summ.get("top_savings_opportunity")
                if isinstance(tso, dict) and tso.get("recommendation_id"):
                    tops = [tso]
        sav_items = [
            SavingsSummaryItem(
                label=(t.get("summary") or t.get("recommendation_type") or "Opportunity")[:80],
                amount=float(t["estimated_savings"]) if t.get("estimated_savings") is not None else None,
                entity_type="recommendation",
                entity_id=str(t["recommendation_id"]) if t.get("recommendation_id") else None,
                reason=f"Risk {t.get('risk_level') or 'n/a'}",
            )
            for t in tops[:3]
        ]
        if sav_items:
            sections.append(
                ResponseSection(
                    section_type="savings_summary",
                    title="Top Savings Opportunities",
                    items=[i.model_dump() for i in sav_items],
                )
            )
        cost_trends = scoped.get("cost_trends")
        if isinstance(cost_trends, list):
            trend_items = []
            for t in cost_trends[:5]:
                pct = t.get("percent_change")
                pct_f = float(pct) if pct is not None else None
                direction = "flat"
                if pct_f is not None:
                    direction = "up" if pct_f > 0 else ("down" if pct_f < 0 else "flat")
                trend_items.append(
                    TrendItem(
                        service=str(t.get("service") or "Unknown"),
                        direction=direction,  # type: ignore[arg-type]
                        percent_change=pct_f,
                        label=t.get("trend"),
                    )
                )
            if trend_items:
                sections.append(
                    ResponseSection(
                        section_type="trends_summary",
                        title="Cost Trends",
                        items=[i.model_dump() for i in trend_items],
                    )
                )

    elif intent == "tenants":
        directory = scoped.get("tenant_directory") or []
        trouble = [d for d in directory if (d.get("cloud_accounts_with_connection_issues") or 0) > 0]
        if trouble:
            sections.append(
                ResponseSection(
                    section_type="warnings",
                    title="Tenants with Connection Issues",
                    items=[
                        WarningItem(
                            message=(
                                f"{d.get('tenant_name') or 'Unknown'}: "
                                f"{d.get('cloud_accounts_with_connection_issues')} account(s) with connection issues"
                            ),
                            severity="warning",
                        ).model_dump()
                        for d in trouble[:5]
                    ],
                )
            )
        summary_counts["tenants_with_issues"] = len(trouble)
        cur = scoped.get("current_cloud_summary") or {}
        if isinstance(cur, dict) and not cur.get("error"):
            tso = cur.get("top_savings_opportunity")
            if isinstance(tso, dict) and tso.get("summary"):
                sections.append(
                    ResponseSection(
                        section_type="narrative",
                        title="Current Account",
                        items=[
                            NarrativeItem(
                                text=f"Top opportunity: {tso['summary'][:200]}"
                            ).model_dump()
                        ],
                    )
                )

    else:  # general_summary
        summ = scoped.get("summary") or {}
        metric_items = []
        if isinstance(summ, dict) and not summ.get("error"):
            metric_items.append(
                MetricItem(
                    label="Total recommendations",
                    value=int(summ.get("total_recommendations") or 0),
                )
            )
            if summ.get("total_estimated_monthly_savings") is not None:
                metric_items.append(
                    MetricItem(
                        label="Est. monthly savings opportunity",
                        value=float(summ["total_estimated_monthly_savings"]),
                        unit="USD/mo",
                    )
                )
            if summ.get("total_cost") is not None:
                metric_items.append(
                    MetricItem(
                        label="Rolling 30d spend",
                        value=float(summ["total_cost"]),
                        unit="USD",
                    )
                )
        if metric_items:
            sections.append(
                ResponseSection(
                    section_type="metrics",
                    title="Account Overview",
                    items=[i.model_dump() for i in metric_items],
                )
            )
        tops = scoped.get("top_opportunities") or []
        if tops:
            gr_items = [
                GroupedRecommendationItem(
                    title=(t.get("summary") or t.get("recommendation_type") or "Opportunity")[:80],
                    group="Top Opportunities",
                    count=1,
                    impact=_risk_to_impact(t.get("risk_level")),  # type: ignore[arg-type]
                    reason=(
                        f"Est. ~${float(t['estimated_savings']):,.2f}/mo"
                        if t.get("estimated_savings") is not None
                        else "Estimate in data"
                    ),
                )
                for t in tops[:3]
            ]
            sections.append(
                ResponseSection(
                    section_type="grouped_recommendations",
                    title="Top Opportunities",
                    items=[i.model_dump() for i in gr_items],
                )
            )

    _title_map = {
        "prioritize": "Top Priorities",
        "blockers": "Active Blockers",
        "approvals": "Pending Approvals",
        "executions": "Execution Status",
        "savings": "Savings Summary",
        "tenants": "Tenant Overview",
        "general_summary": "Account Summary",
    }
    return StructuredAIResponse(
        response_type=intent,
        title=_title_map.get(intent, intent.replace("_", " ").title()),
        summary=ResponseSummary(counts=summary_counts),
        sections=sections,
        meta=ResponseMeta(response_type=intent),
    )


def _rules_from_scored_attention(
    scored: list[dict[str, Any]],
    *,
    lead_sentence: str,
) -> tuple[list[CopilotPriorityAction], list[str], list[str]]:
    """Map pre-scored attention rows to copilot actions + insights + answer lines."""
    actions: list[CopilotPriorityAction] = []
    insights: list[str] = []
    lines: list[str] = []
    if lead_sentence:
        lines.append(lead_sentence)
    for row in scored[:_MAX_COPILOT_PRIORITIES]:
        et = _entity_type_ok(row.get("entity_type"))
        eid = row.get("entity_id")
        at = _action_type_ok(row.get("action_type")) or "investigate"
        ns = _NEXT_STEP_BY_TYPE.get(at, _NEXT_STEP_BY_TYPE["investigate"])
        actions.append(
            CopilotPriorityAction(
                title=str(row.get("title") or "Attention item")[:500],
                reason=str(row.get("why_action_needed") or row.get("top_reason") or "")[:2000],
                next_step=ns,
                action_type=at,
                entity_type=et,
                entity_id=str(eid) if eid else None,
            )
        )
        insights.append(ns)
    if scored:
        first = scored[0]
        lines.append(
            f"Lead item — {first.get('title', '')}: {first.get('why_action_needed', '')[:280]}"
        )
    else:
        lines.append("No actionable attention items matched this view.")
    return actions, insights[:_MAX_INSIGHTS_FINAL], lines


def _rules_based_response(
    intent: str,
    scoped: dict[str, Any],
    query: str,
    *,
    effective_access: str,
) -> CopilotQueryResponse:
    """Deterministic answers when no LLM is configured."""
    qlow = (query or "").lower()
    access = (effective_access or "none").lower()
    actions: list[CopilotPriorityAction] = []
    insights: list[str] = []
    lines: list[str] = []

    if intent == "prioritize":
        scored = scoped.get("scored_attention_top") or []
        actions, insights, lines = _rules_from_scored_attention(scored, lead_sentence="")

    elif intent == "blockers":
        scored = scoped.get("scored_attention_top") or []
        actions, insights, lines = _rules_from_scored_attention(scored, lead_sentence="")

    elif intent == "approvals":
        items = scoped.get("pending_approvals") or []
        lines.append(
            "Prioritize approvals by risk, savings upside, and preflight/dry-run confidence — not FIFO."
        )
        for pa in items[:_MAX_COPILOT_PRIORITIES]:
            bits: list[str] = []
            if pa.get("risk_level"):
                bits.append(f"risk {pa['risk_level']}")
            if pa.get("estimated_savings") is not None:
                bits.append(f"~${float(pa['estimated_savings']):,.2f}/mo upside")
            if pa.get("preflight_status"):
                bits.append(f"preflight {pa['preflight_status']}")
            reason = (
                "; ".join(bits) + f" — {pa.get('approvals_complete', 0)}/{pa.get('approvals_required', 0)} approvals recorded."
                if bits
                else f"Waiting on approvers ({pa.get('approvals_complete', 0)}/{pa.get('approvals_required', 0)})."
            )
            actions.append(
                CopilotPriorityAction(
                    title=(pa.get("summary") or "Pending approval")[:100],
                    reason=reason[:500],
                    entity_type="approval_request",
                    entity_id=pa.get("approval_request_id"),
                )
            )
        insights.append("Go to Approvals; expand preflight and dry-run; approve only when impact matches policy.")
        if len(items) > _MAX_COPILOT_PRIORITIES:
            insights.append(f"{len(items)} total open — after the top {_MAX_COPILOT_PRIORITIES}, continue in risk/savings order.")

    elif intent == "executions":
        items = scoped.get("ready_to_execute") or []
        not_ready = scoped.get("not_ready_to_execute") or []
        lines.append(
            "Execution work is about eligibility: run only what policy and gates allow; unblock the rest with evidence."
        )
        merged = list(items) + list(not_ready)
        for r in merged[:_MAX_COPILOT_PRIORITIES]:
            ee = r.get("execution_eligibility") or {}
            elig = bool(ee.get("execution_eligible"))
            actions.append(
                CopilotPriorityAction(
                    title=(r.get("summary") or r.get("recommendation_type") or "Recommendation")[:90],
                    reason=(
                        (ee.get("blocking_reason") or ee.get("display_label") or "Not eligible yet.")
                        if not elig
                        else (ee.get("display_label") or "Eligible — apply when your change window allows.")
                    )[:500],
                    entity_type="recommendation",
                    entity_id=r.get("recommendation_id"),
                )
            )
        insights.append("Open each recommendation; confirm preflight and Run fix only when eligibility shows ready.")
        if not_ready:
            insights.append("For blocked rows, read blocking_reason, fix prerequisites, then re-check eligibility.")

    elif intent == "savings":
        acc = scoped.get("savings_proof_account") or {}
        ten = scoped.get("savings_proof_tenant") or {}
        lines.append(
            "Use proof-of-savings and cost trends to validate where realized impact shows up — prioritize accounts/services that moved."
        )
        if acc.get("total_estimated_monthly_savings_proof") is not None:
            insights.append(
                f"Account proof roll-up (~${float(acc.get('total_estimated_monthly_savings_proof') or 0):,.2f}/mo in-window) — compare to Recommendations for the next changes to ship."
            )
        if ten.get("total_estimated_monthly_savings_proof") is not None:
            insights.append(
                f"Tenant roll-up ~${float(ten.get('total_estimated_monthly_savings_proof') or 0):,.2f}/mo — use tenant view for customers lagging proof."
            )
        tops = scoped.get("top_opportunities") or []
        if not tops:
            summ = scoped.get("summary") or {}
            if isinstance(summ, dict):
                tso = summ.get("top_savings_opportunity")
                if isinstance(tso, dict) and tso.get("recommendation_id"):
                    tops = [
                        {
                            "recommendation_id": tso.get("recommendation_id"),
                            "summary": tso.get("summary"),
                            "recommendation_type": None,
                            "estimated_savings": tso.get("estimated_savings"),
                            "risk_level": tso.get("risk_level"),
                        }
                    ]
        for t in tops[:_MAX_COPILOT_PRIORITIES]:
            if len(actions) >= _MAX_COPILOT_PRIORITIES:
                break
            est = t.get("estimated_savings")
            est_s = f"~${float(est):,.2f}/mo est." if est is not None else "estimate in data"
            actions.append(
                CopilotPriorityAction(
                    title=f"Ship next: {(t.get('summary') or t.get('recommendation_type') or '')[:80]}",
                    reason=f"{est_s}; risk {t.get('risk_level') or 'n/a'} — strongest candidate from ranked opportunities.",
                    entity_type="recommendation",
                    entity_id=str(t.get("recommendation_id")) if t.get("recommendation_id") else None,
                )
            )
        trends = scoped.get("cost_trends")
        if isinstance(trends, list):
            for t in trends[:3]:
                insights.append(f"Cost trend — {t.get('service')}: {t.get('trend')} ({t.get('percent_change')}% WoW).")

    elif intent == "tenants":
        directory = scoped.get("tenant_directory") or []
        lines.append("Give attention to tenants with broken AWS connections or weak proof — they hide risk and savings.")
        trouble = [d for d in directory if (d.get("cloud_accounts_with_connection_issues") or 0) > 0]
        for d in trouble[:_MAX_COPILOT_PRIORITIES]:
            actions.append(
                CopilotPriorityAction(
                    title=f"Tenant: {d.get('tenant_name') or 'Unknown'}",
                    reason=f"{d.get('cloud_accounts_with_connection_issues')} account(s) with connection issues — data may be incomplete.",
                    entity_type="tenant",
                    entity_id=str(d.get("tenant_id")) if d.get("tenant_id") else None,
                )
            )
        for d in directory:
            if d.get("is_current_tenant"):
                insights.append(
                    f"You are on {d.get('tenant_name')}: validate its accounts first, then scan other tenants for connection gaps."
                )
                break
        if not insights:
            insights.append("Switch tenant context in the header and validate each account’s connection status.")
        cur = scoped.get("current_cloud_summary") or {}
        if isinstance(cur, dict) and not cur.get("error") and cur.get("top_savings_opportunity"):
            tso = cur["top_savings_opportunity"]
            if isinstance(tso, dict) and tso.get("summary"):
                insights.append(f"Current account top opportunity to socialize: {tso.get('summary')[:120]}")

    else:  # general_summary — user routed here for an overview-style question
        summ = scoped.get("summary") or {}
        if isinstance(summ, dict) and not summ.get("error"):
            lines.append(
                f"Overview: {summ.get('total_recommendations', 0)} recommendations; "
                f"~${float(summ.get('total_estimated_monthly_savings') or 0):,.2f}/mo savings opportunity vs "
                f"~${float(summ.get('total_cost') or 0):,.2f} rolling spend ({summ.get('cost_window_label', '')})."
            )
            tops = scoped.get("top_opportunities") or []
            for t in tops[:_MAX_COPILOT_PRIORITIES]:
                if len(actions) >= _MAX_COPILOT_PRIORITIES:
                    break
                est = t.get("estimated_savings")
                est_s = f"~${float(est):,.2f}/mo est." if est is not None else "estimate n/a"
                actions.append(
                    CopilotPriorityAction(
                        title=(t.get("summary") or t.get("recommendation_type") or "Opportunity")[:80],
                        reason=f"{est_s}; risk {t.get('risk_level') or 'n/a'}.",
                        entity_type="recommendation",
                        entity_id=str(t.get("recommendation_id")) if t.get("recommendation_id") else None,
                    )
                )
            insights.append("Next: open Recommendations for detail, route high-impact items through Approvals, then apply when eligible.")
        else:
            lines.append("Limited summary data for this account; try refreshing sync or validating the connection.")
            insights.append("Validate the cloud connection, re-run sync, then re-ask once findings refresh.")

    answer = " ".join(lines).strip() or "No matching operational data in this snapshot for that question."
    if access == "viewer" and intent not in ("general_summary", "savings", "tenants"):
        insights.append("Your access is viewer — escalate approvals and execution to an approver or admin.")
    if any(k in qlow for k in ("why", "how", "explain")) and intent != "general_summary":
        insights.append("Tie explanations to the fields shown in scoped_platform_data — do not speculate beyond them.")

    return _postprocess_copilot_response(
        CopilotQueryResponse(
            answer=answer[:8000],
            priority_actions=actions,
            insights=insights,
        )
    )


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = text.strip()
    m = re.search(r"\{[\s\S]*\}\s*$", raw)
    if m:
        raw = m.group(0)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _parse_llm_payload(data: dict[str, Any]) -> CopilotQueryResponse | None:
    try:
        answer = str(data.get("answer") or "").strip()
        if not answer:
            return None
        pa_raw = data.get("priority_actions") or []
        if not isinstance(pa_raw, list):
            pa_raw = []
        actions: list[CopilotPriorityAction] = []
        for item in pa_raw:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            et = _entity_type_ok(item.get("entity_type"))
            eid = item.get("entity_id")
            at = _action_type_ok(item.get("action_type"))
            actions.append(
                CopilotPriorityAction(
                    title=title[:500],
                    reason=str(item.get("reason") or item.get("detail") or "")[:2000],
                    next_step=str(item.get("next_step") or "")[:1000],
                    action_type=at,
                    entity_type=et,
                    entity_id=str(eid) if eid else None,
                )
            )
        ins = data.get("insights") or []
        if not isinstance(ins, list):
            ins = []
        return CopilotQueryResponse(
            answer=answer[:8000],
            priority_actions=actions,
            insights=[str(x)[:1000] for x in ins if str(x).strip()],
        )
    except Exception:
        return None


def _call_openai_with_pack(user_payload: dict[str, Any]) -> CopilotQueryResponse | None:
    key = (settings.openai_api_key or "").strip()
    if not key:
        return None
    base = (settings.openai_base_url or "https://api.openai.com/v1").rstrip("/")
    model = (settings.copilot_model or "gpt-4o-mini").strip()
    url = f"{base}/chat/completions"
    user_content = json.dumps(user_payload, default=str)
    payload: dict[str, Any] = {
        "model": model,
        "temperature": 0.15,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    }
    if model.startswith("gpt-4") or model.startswith("o"):
        payload["response_format"] = {"type": "json_object"}

    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.post(
                url,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
            )
            r.raise_for_status()
            body = r.json()
    except Exception:
        logger.exception("Copilot OpenAI request failed")
        return None

    try:
        text = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    parsed = _extract_json_object(text)
    if not parsed:
        return None
    return _parse_llm_payload(parsed)


def _merge_debug(
    base: CopilotDebugInfo | None,
    *,
    items_sent: int | None,
    grouping_counts: dict[str, int] | None,
) -> CopilotDebugInfo | None:
    if base is None:
        return None
    return base.model_copy(
        update={
            "items_sent_to_llm": items_sent,
            "grouping_counts": grouping_counts,
        }
    )


def run_copilot_query(
    db: Session,
    *,
    organization_id: UUID,
    tenant_id: UUID,
    cloud_account_id: UUID,
    effective_operational_access: str,
    org_membership_role: str,
    query: str,
) -> CopilotQueryResponse:
    intent, scoped = run_intent_and_scoped_context(
        db,
        query=query,
        organization_id=organization_id,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        effective_operational_access=effective_operational_access,
        org_membership_role=org_membership_role,
    )
    n_items = count_context_payload_items(scoped)
    q = query.strip()

    if intent == "low_priority":
        answer, structured, n_eval = build_low_priority_copilot_answer(
            db,
            organization_id=organization_id,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
        )
        resp = CopilotQueryResponse(answer=answer, priority_actions=[], insights=[], structured=structured)
        if settings.debug:
            resp = resp.model_copy(
                update={
                    "debug": CopilotDebugInfo(
                        detected_intent=intent,
                        context_item_count=n_eval,
                        items_sent_to_llm=0,
                        grouping_counts=None,
                    )
                }
            )
        return resp

    # ── Fallback detection ─────────────────────────────────────────────────────
    # Classify feasibility before spending cycles on LLM or rules.
    feasibility = classify_query_feasibility(q, intent, scoped)
    if feasibility != "supported":
        fallback = build_fallback_response(feasibility, q, intent)
        resp = CopilotQueryResponse(
            answer=fallback.message,
            priority_actions=[],
            insights=list(fallback.suggestions[:3]),
            fallback=fallback,
        )
        if settings.debug:
            resp = resp.model_copy(
                update={
                    "debug": CopilotDebugInfo(
                        detected_intent=intent,
                        context_item_count=n_items,
                        items_sent_to_llm=0,
                        grouping_counts=None,
                    )
                }
            )
        return resp

    debug: CopilotDebugInfo | None = None
    if settings.debug:
        debug = CopilotDebugInfo(detected_intent=intent, context_item_count=n_items)

    items_sent: int | None = None
    grouping_counts: dict[str, int] | None = None
    llm_pack: dict[str, Any]

    if intent in ("prioritize", "blockers"):
        scored = scoped.get("scored_attention_top") or []
        pack_body, meta = build_attention_llm_pack(
            scored,
            intent=intent,
            user_question=q,
            scope=scoped.get("scope") if isinstance(scoped.get("scope"), dict) else None,
        )
        items_sent = int(meta.get("items_sent_to_llm", 0))
        grouping_counts = meta.get("grouping_counts")
        llm_pack = pack_body
    else:
        trimmed = trim_generic_scoped_for_llm(scoped, intent=intent)
        items_sent = _count_trimmed_scoped_items(trimmed)
        llm_pack = {
            "user_question": q,
            "detected_intent": intent,
            "scoped_data": trimmed,
        }

    raw_llm = _call_openai_with_pack(llm_pack)
    llm_out: CopilotQueryResponse | None = None
    if raw_llm is not None:
        llm_out = _postprocess_copilot_response(raw_llm)
    if _llm_response_is_weak(llm_out):
        if intent in ("prioritize", "blockers"):
            scored_fb = scoped.get("scored_attention_top") or []
            fallback_resp = _postprocess_copilot_response(
                _fallback_attention_response(
                    scored_fb,
                    intent=intent,
                    effective_access=effective_operational_access,
                )
            )
            fallback_structured = _fallback_attention_structured(scored_fb, intent=intent)
            llm_out = fallback_resp.model_copy(update={"structured": fallback_structured})
        else:
            llm_out = None

    if llm_out is not None:
        if debug:
            llm_out = llm_out.model_copy(update={"debug": _merge_debug(debug, items_sent=items_sent, grouping_counts=grouping_counts)})
        return llm_out

    rules = _rules_based_response(
        intent,
        scoped,
        query,
        effective_access=effective_operational_access,
    )
    rules_structured = _rules_based_structured(intent, scoped)
    rules = rules.model_copy(update={"structured": rules_structured})
    if debug:
        rules = rules.model_copy(update={"debug": _merge_debug(debug, items_sent=items_sent, grouping_counts=grouping_counts)})
    return rules
