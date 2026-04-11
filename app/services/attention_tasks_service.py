"""Gather and score account-level attention items for /me/tasks and Copilot."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.services.attention_scoring_service import score_attention_candidates
from app.services.copilot_context_service import (
    _enrich_approvals_preflight_dryrun,
    _enrich_execution_eligibility,
    _failed_execution_signals,
    _pending_approvals_list,
    _ready_to_execute_list,
    _rejected_approval_requests,
    _top_opportunities,
)

BLOCKER_ITEM_KINDS = frozenset(
    {
        "failed_execution",
        "approval_partial",
        "approval_rejected",
        "execution_blocked",
    }
)


def _gather_raw_candidates(
    db: Session,
    organization_id: UUID,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> list[dict]:
    candidates: list[dict] = []

    # NOTE: failed_sync is intentionally excluded here.
    # Sync failures are a Meezi infrastructure issue, not an AWS resource
    # optimization opportunity. They are already surfaced in the sync history
    # panel (WorkspaceContextSettings). Including them in "Top attention"
    # alongside resource opportunities is misleading and pollutes the ranking.

    for row in _failed_execution_signals(db, tenant_id, cloud_account_id, 15):
        candidates.append(
            {
                "kind": "failed_execution",
                "title": f"Execution issue: {(row.get('execution_notes_excerpt') or row.get('impact_status') or 'regression')[:90]}",
                "entity_type": "recommendation",
                "entity_id": row.get("recommendation_id"),
                "recommendation_id": row.get("recommendation_id"),
                "estimated_monthly_savings": None,
                "risk_level": None,
                "preflight_status": None,
                "status": None,
                "execution_eligible": False,
                "blocking_reason": row.get("impact_status"),
                "error_message": None,
                "execution_notes_excerpt": row.get("execution_notes_excerpt"),
                "anchor_iso": row.get("updated_at"),
            }
        )

    pendings = _pending_approvals_list(db, organization_id, tenant_id, cloud_account_id, 25)
    _enrich_approvals_preflight_dryrun(db, tenant_id, cloud_account_id, pendings)

    rec_seen: set[str] = set()

    for pa in pendings:
        st = (pa.get("status") or "").strip().lower()
        if st == "partially_approved":
            kind = "approval_partial"
        else:
            kind = "approval_pending"
        rid = pa.get("recommendation_id")
        if rid:
            rec_seen.add(str(rid))
        candidates.append(
            {
                "kind": kind,
                "title": f"Approval: {(pa.get('summary') or 'Pending request')[:100]}",
                "entity_type": "approval_request",
                "entity_id": pa["approval_request_id"],
                "recommendation_id": rid,
                "estimated_monthly_savings": pa.get("estimated_savings"),
                "risk_level": pa.get("risk_level"),
                "preflight_status": pa.get("preflight_status"),
                "status": pa.get("status"),
                "execution_eligible": None,
                "blocking_reason": None,
                "error_message": None,
                "execution_notes_excerpt": None,
                "anchor_iso": pa.get("submitted_at"),
            }
        )

    for rj in _rejected_approval_requests(db, organization_id, tenant_id, cloud_account_id, 10):
        rid = rj.get("recommendation_id")
        if rid:
            rec_seen.add(str(rid))
        candidates.append(
            {
                "kind": "approval_rejected",
                "title": f"Rejected: {(rj.get('summary') or 'Approval')[:100]}",
                "entity_type": "approval_request",
                "entity_id": rj["approval_request_id"],
                "recommendation_id": rid,
                "estimated_monthly_savings": None,
                "risk_level": None,
                "preflight_status": None,
                "status": rj.get("status"),
                "execution_eligible": None,
                "blocking_reason": None,
                "error_message": None,
                "execution_notes_excerpt": None,
                "anchor_iso": rj.get("rejected_at"),
            }
        )

    ready = _ready_to_execute_list(db, tenant_id, cloud_account_id, 15)
    _enrich_execution_eligibility(db, organization_id, tenant_id, cloud_account_id, ready)
    for r in ready:
        ee = r.get("execution_eligibility") or {}
        elig = bool(ee.get("execution_eligible"))
        kind = "execution_ready" if elig else "execution_blocked"
        rid = r.get("recommendation_id")
        if rid:
            rec_seen.add(str(rid))
        candidates.append(
            {
                "kind": kind,
                "title": f"Execution: {(r.get('summary') or r.get('recommendation_type') or '')[:100]}",
                "entity_type": "recommendation",
                "entity_id": rid,
                "recommendation_id": rid,
                "estimated_monthly_savings": r.get("estimated_savings"),
                "risk_level": r.get("risk_level"),
                "preflight_status": None,
                "status": None,
                "execution_eligible": elig,
                "blocking_reason": ee.get("blocking_reason"),
                "error_message": None,
                "execution_notes_excerpt": None,
                "anchor_iso": r.get("approved_at"),
            }
        )

    for t in _top_opportunities(db, tenant_id, cloud_account_id, 8):
        rid = str(t.get("recommendation_id") or "")
        if not rid or rid in rec_seen:
            continue
        est = t.get("estimated_savings")
        candidates.append(
            {
                "kind": "recommendation_top",
                "title": f"Opportunity: {(t.get('summary') or t.get('recommendation_type') or '')[:100]}",
                "entity_type": "recommendation",
                "entity_id": rid,
                "recommendation_id": rid,
                "estimated_monthly_savings": float(est) if est is not None else None,
                "risk_level": t.get("risk_level"),
                "preflight_status": None,
                "status": None,
                "execution_eligible": None,
                "blocking_reason": None,
                "error_message": None,
                "execution_notes_excerpt": None,
                "anchor_iso": None,
            }
        )

    return candidates


def score_account_attention_items(
    db: Session,
    organization_id: UUID,
    tenant_id: UUID,
    cloud_account_id: UUID,
    *,
    limit: int = 40,
) -> list[dict]:
    raw = _gather_raw_candidates(db, organization_id, tenant_id, cloud_account_id)
    scored = score_attention_candidates(raw)
    return scored[:limit]


def score_account_blockers_subset(
    db: Session,
    organization_id: UUID,
    tenant_id: UUID,
    cloud_account_id: UUID,
    *,
    limit: int = 12,
) -> list[dict]:
    full = score_account_attention_items(db, organization_id, tenant_id, cloud_account_id, limit=80)
    filtered = [x for x in full if x.get("item_kind") in BLOCKER_ITEM_KINDS]
    return filtered[:limit]
