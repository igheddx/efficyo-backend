"""Score operational attention items (impact, urgency, risk, readiness, friction)."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

# Default composite weights: impact, urgency, risk, readiness, friction
_WEIGHT_DEFAULT = (0.30, 0.25, 0.15, 0.20, 0.10)
_WEIGHT_APPROVALS = (0.25, 0.30, 0.20, 0.15, 0.10)
_WEIGHT_EXECUTION = (0.35, 0.20, 0.15, 0.25, 0.05)
_WEIGHT_FAILURES = (0.20, 0.40, 0.10, 0.05, 0.25)
_WEIGHT_RECOMMENDATION = (0.40, 0.20, 0.20, 0.15, 0.05)


def friction_score_from_age(anchor: datetime | None, now: datetime) -> float:
    """Staleness / age friction: 0–1d=10, 2–3d=30, 4–7d=60, 8+=90. Unknown anchor → moderate."""
    if anchor is None:
        return 45.0
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    delta = now - anchor
    if delta.total_seconds() < 0:
        return 10.0
    days = max(0.0, delta.total_seconds() / 86400.0)
    if days <= 1:
        return 10.0
    if days <= 3:
        return 30.0
    if days <= 7:
        return 60.0
    return 90.0


def _parse_anchor(iso: str | None) -> datetime | None:
    if not iso or not isinstance(iso, str):
        return None
    s = iso.strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def risk_level_to_score(risk_level: str | None) -> float:
    r = (risk_level or "").strip().lower()
    if r == "high":
        return 100.0
    if r == "medium":
        return 60.0
    if r == "low":
        return 30.0
    return 45.0


def _urgency_for_kind(kind: str) -> float:
    return {
        "failed_execution": 100.0,
        "failed_sync": 90.0,
        "approval_pending": 80.0,
        "execution_ready": 70.0,
        "execution_blocked": 72.0,
        "approval_partial": 60.0,
        "approval_rejected": 50.0,
        "recommendation_top": 30.0,
    }.get(kind, 35.0)


def _readiness_for_candidate(c: dict[str, Any]) -> float:
    kind = c.get("kind") or ""
    if kind == "failed_sync":
        return 12.0
    if kind == "failed_execution":
        return 22.0
    if kind == "approval_rejected":
        return 25.0
    if kind == "approval_partial":
        return 70.0
    if kind == "approval_pending":
        ps = str(c.get("preflight_status") or "").lower()
        if any(x in ps for x in ("fail", "unsafe", "error", "blocked")):
            return 40.0
        return 85.0
    if kind in ("execution_ready", "execution_blocked"):
        if c.get("execution_eligible"):
            return 100.0
        return 48.0
    if kind == "recommendation_top":
        return 55.0
    return 50.0


def _weights_for_kind(kind: str) -> tuple[float, float, float, float, float]:
    if kind in ("failed_sync", "failed_execution"):
        return _WEIGHT_FAILURES
    if kind.startswith("approval"):
        return _WEIGHT_APPROVALS
    if kind.startswith("execution"):
        return _WEIGHT_EXECUTION
    if kind == "recommendation_top":
        return _WEIGHT_RECOMMENDATION
    return _WEIGHT_DEFAULT


def _normalize_impact(
    kind: str,
    estimated_monthly_savings: float | None,
    max_savings: float,
) -> float:
    if kind == "failed_sync":
        return 92.0
    if kind == "failed_execution":
        return 78.0
    if estimated_monthly_savings is None or math.isnan(estimated_monthly_savings):
        if kind in ("approval_pending", "approval_partial", "approval_rejected"):
            return 32.0
        if kind in ("execution_ready", "execution_blocked"):
            return 38.0
        if kind == "recommendation_top":
            return 22.0
        return 28.0
    if max_savings <= 0:
        return min(100.0, float(estimated_monthly_savings))
    return min(100.0, 100.0 * float(estimated_monthly_savings) / max_savings)


def _priority_bucket(score: float) -> str:
    if score >= 76.0:
        return "critical"
    if score >= 58.0:
        return "high"
    if score >= 40.0:
        return "medium"
    return "low"


def _why_action(kind: str, c: dict[str, Any]) -> str:
    if kind == "failed_sync":
        em = (c.get("error_message") or "")[:160]
        base = "Sync/ingest failed — recommendations and inventory may be stale until this is fixed."
        return f"{base} ({em})" if em else base
    if kind == "failed_execution":
        ex = (c.get("execution_notes_excerpt") or "")[:160]
        base = "Execution or regression signal — understand what failed before approving similar changes."
        return f"{base} ({ex})" if ex else base
    if kind == "approval_pending":
        return "Approval is blocking execution for this recommendation — reviewers need to decide."
    if kind == "approval_partial":
        return "Partially approved — remaining approvers must act or the workflow stalls."
    if kind == "approval_rejected":
        return "Rejected approval — needs rework or policy discussion before resubmission."
    if kind == "execution_ready":
        return "Approved and eligible to apply — pending application in your change window."
    if kind == "execution_blocked":
        br = (c.get("blocking_reason") or c.get("blocking_reason_short") or "")[:180]
        base = "Approved but not eligible to run yet — clear gating issues first."
        return f"{base} ({br})" if br else base
    if kind == "recommendation_top":
        return "High-impact opportunity not yet in workflow — route through review and approval when ready."
    return "Operational follow-up required for this item."


def infer_action_type(row: dict[str, Any]) -> str:
    """
    Map scored row → Copilot action_type for UX and LLM phrasing.
    execute_now | approve | review | investigate | fix_failure
    """
    kind = str(row.get("item_kind") or "")
    imp = float(row.get("impact_score") or 0)
    elig = row.get("execution_eligible")
    if kind in ("failed_sync", "failed_execution"):
        return "fix_failure"
    if kind == "execution_ready" and elig is True:
        return "execute_now"
    if kind in ("execution_blocked",):
        return "investigate"
    if kind in ("approval_pending", "approval_partial"):
        return "approve"
    if kind == "approval_rejected":
        return "investigate"
    if kind == "recommendation_top":
        if imp >= 60:
            return "review"
        return "investigate"
    if kind == "execution_ready":
        return "investigate"
    return "investigate"


def _top_reason_line(
    kind: str,
    impact: float,
    urgency: float,
    risk: float,
    readiness: float,
    friction: float,
) -> str:
    ranked = sorted(
        [
            ("impact/savings signal", impact),
            ("urgency", urgency),
            ("risk", risk),
            ("readiness to act", readiness),
            ("staleness/friction", friction),
        ],
        key=lambda x: x[1],
        reverse=True,
    )
    lead, val = ranked[0]
    return f"Scoring is led by {lead} ({val:.0f}/100) for this {kind.replace('_', ' ')}."


def score_attention_candidates(
    candidates: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """
    Each candidate dict expects keys:
    kind, title, entity_type, entity_id, recommendation_id (optional),
    estimated_monthly_savings, risk_level, preflight_status, status,
    execution_eligible, blocking_reason, error_message, execution_notes_excerpt, anchor_iso.
    """
    if not candidates:
        return []
    now = now or datetime.now(timezone.utc)
    savings_vals = [
        float(c["estimated_monthly_savings"])
        for c in candidates
        if c.get("estimated_monthly_savings") is not None
        and not math.isnan(float(c["estimated_monthly_savings"]))
    ]
    max_savings = max(savings_vals) if savings_vals else 0.0

    scored: list[dict[str, Any]] = []
    for c in candidates:
        kind = str(c.get("kind") or "recommendation_top")
        anchor = _parse_anchor(c.get("anchor_iso"))
        friction = friction_score_from_age(anchor, now)
        risk = risk_level_to_score(c.get("risk_level"))
        urgency = _urgency_for_kind(kind)
        readiness = _readiness_for_candidate(c)
        impact = _normalize_impact(kind, c.get("estimated_monthly_savings"), max_savings)
        w = _weights_for_kind(kind)
        priority = (
            w[0] * impact + w[1] * urgency + w[2] * risk + w[3] * readiness + w[4] * friction
        )
        priority = min(100.0, max(0.0, priority))
        bucket = _priority_bucket(priority)
        why = _why_action(kind, c)
        top_r = _top_reason_line(kind, impact, urgency, risk, readiness, friction)
        scored.append(
            {
                "item_kind": kind,
                "entity_type": c.get("entity_type"),
                "entity_id": c.get("entity_id"),
                "recommendation_id": c.get("recommendation_id"),
                "title": (c.get("title") or "Attention item")[:500],
                "priority_score": round(priority, 2),
                "priority_bucket": bucket,
                "why_action_needed": why[:2000],
                "top_reason": top_r[:500],
                "impact_score": round(impact, 2),
                "urgency_score": round(urgency, 2),
                "risk_score": round(risk, 2),
                "readiness_score": round(readiness, 2),
                "friction_score": round(friction, 2),
                "execution_eligible": c.get("execution_eligible"),
            }
        )

    best_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    without_id: list[dict[str, Any]] = []
    for row in scored:
        et = str(row.get("entity_type") or "")
        eid = str(row.get("entity_id") or "")
        if not eid:
            without_id.append(row)
            continue
        key = (et, eid)
        prev = best_by_key.get(key)
        if prev is None or float(row["priority_score"]) > float(prev["priority_score"]):
            best_by_key[key] = row

    out = list(best_by_key.values()) + without_id
    out.sort(key=lambda r: float(r["priority_score"]), reverse=True)
    for row in out:
        row["action_type"] = infer_action_type(row)
    return out
