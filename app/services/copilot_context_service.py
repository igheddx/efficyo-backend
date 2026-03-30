"""Assemble scoped, read-only platform facts for the Operations Copilot (intent-driven)."""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import desc, or_
from sqlalchemy.orm import Session

from app.models.approval_request import ApprovalRequest
from app.models.cloud_account import CloudAccount
from app.models.recommendation import Recommendation
from app.models.recommendation_outcome import RecommendationOutcome
from app.models.tenant import Tenant
from app.services import (
    approval_request_service,
    ingestion_job_service,
    recommendation_outcome_service,
    recommendation_service,
    summary_service,
    trend_service,
)
from app.services.copilot_intent_service import COPILOT_INTENTS, classify_copilot_intent
from app.services.execution_eligibility_service import compute_execution_eligibility

logger = logging.getLogger(__name__)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _scope_block(
    *,
    organization_id: UUID,
    tenant_id: UUID,
    cloud_account_id: UUID,
    effective_operational_access: str,
    org_membership_role: str,
    tenant: Tenant | None,
    cloud: CloudAccount | None,
) -> dict:
    return {
        "organization_id": str(organization_id),
        "tenant_id": str(tenant_id),
        "tenant_name": tenant.name if tenant else None,
        "cloud_account_id": str(cloud_account_id),
        "cloud_account_name": cloud.name if cloud else None,
        "aws_account_id": cloud.account_id if cloud else None,
        "effective_operational_access": effective_operational_access,
        "org_membership_role": org_membership_role,
    }


def _connection_block(cloud: CloudAccount | None) -> dict | None:
    if cloud is None:
        return None
    return {
        "connection_status": cloud.connection_status,
        "last_validated_at": _iso(cloud.last_validated_at),
        "last_validation_error": (cloud.last_validation_error or "")[:500] or None,
    }


def _pending_approvals_list(db: Session, organization_id: UUID, tenant_id: UUID, cloud_account_id: UUID, limit: int) -> list[dict]:
    open_statuses = approval_request_service.OPEN_STATUSES
    rows = (
        db.query(ApprovalRequest)
        .filter(
            ApprovalRequest.organization_id == organization_id,
            ApprovalRequest.tenant_id == tenant_id,
            ApprovalRequest.cloud_account_id == cloud_account_id,
            ApprovalRequest.status.in_(open_statuses),
        )
        .order_by(desc(ApprovalRequest.created_at))
        .limit(limit)
        .all()
    )
    out: list[dict] = []
    for req in rows:
        read = approval_request_service.request_to_read(req, include_assignments=False)
        rec = (
            db.query(Recommendation)
            .filter(
                Recommendation.id == req.recommendation_id,
                Recommendation.tenant_id == tenant_id,
                Recommendation.cloud_account_id == cloud_account_id,
            )
            .first()
        )
        out.append(
            {
                "approval_request_id": str(req.id),
                "recommendation_id": str(req.recommendation_id),
                "status": req.status,
                "submitted_at": _iso(req.submitted_at),
                "approvals_complete": read.get("approvals_complete"),
                "approvals_required": read.get("approvals_required"),
                "summary": approval_request_service.recommendation_summary_for_request(db, req),
                "risk_level": rec.risk_level if rec else None,
                "estimated_savings": float(rec.estimated_savings) if rec and rec.estimated_savings is not None else None,
            }
        )
    return out


def _enrich_approvals_preflight_dryrun(db: Session, tenant_id: UUID, cloud_account_id: UUID, items: list[dict]) -> None:
    from app.services import preflight_dry_run_service

    for pa in items[:3]:
        rid = pa.get("recommendation_id")
        if not rid:
            continue
        try:
            pf = preflight_dry_run_service.run_preflight(db, tenant_id, cloud_account_id, UUID(str(rid)))
            pa["preflight_status"] = pf.get("status")
            pa["preflight_safe_to_apply"] = pf.get("safe_to_apply")
        except Exception:
            logger.debug("copilot preflight enrich failed", exc_info=True)
            pa["preflight_status"] = "unavailable"
    for pa in items[:2]:
        rid = pa.get("recommendation_id")
        if not rid:
            continue
        try:
            dr = preflight_dry_run_service.run_dry_run(db, tenant_id, cloud_account_id, UUID(str(rid)), tag_values=None)
            pa["dry_run_impact_summary"] = dr.get("impact_summary")
        except Exception:
            logger.debug("copilot dry-run enrich failed", exc_info=True)


def _ready_to_execute_list(db: Session, tenant_id: UUID, cloud_account_id: UUID, limit: int) -> list[dict]:
    ready = (
        db.query(RecommendationOutcome, Recommendation)
        .join(Recommendation, Recommendation.id == RecommendationOutcome.recommendation_id)
        .filter(
            RecommendationOutcome.tenant_id == tenant_id,
            RecommendationOutcome.cloud_account_id == cloud_account_id,
            RecommendationOutcome.workflow_status == "approved",
            RecommendationOutcome.status == "pending",
        )
        .order_by(desc(RecommendationOutcome.approved_at))
        .limit(limit)
        .all()
    )
    out: list[dict] = []
    for outcome, rec in ready:
        out.append(
            {
                "recommendation_id": str(outcome.recommendation_id),
                "resource_id": outcome.resource_id,
                "recommendation_type": outcome.recommendation_type,
                "summary": rec.summary,
                "estimated_savings": float(rec.estimated_savings) if rec.estimated_savings is not None else None,
                "risk_level": rec.risk_level,
                "approved_at": _iso(outcome.approved_at),
            }
        )
    return out


def _enrich_execution_eligibility(
    db: Session,
    organization_id: UUID,
    tenant_id: UUID,
    cloud_account_id: UUID,
    items: list[dict],
) -> None:
    for row in items[:10]:
        rid = row.get("recommendation_id")
        if not rid:
            continue
        try:
            el = compute_execution_eligibility(
                db,
                organization_id=organization_id,
                tenant_id=tenant_id,
                cloud_account_id=cloud_account_id,
                recommendation_id=UUID(str(rid)),
            )
            row["execution_eligibility"] = {
                "execution_eligible": el.get("execution_eligible"),
                "blocking_reason": el.get("blocking_reason"),
                "display_label": el.get("display_label"),
                "display_status": el.get("display_status"),
            }
        except Exception:
            logger.debug("copilot eligibility enrich failed", exc_info=True)


def _failed_sync_jobs(db: Session, tenant_id: UUID, cloud_account_id: UUID, limit: int) -> list[dict]:
    try:
        jobs = ingestion_job_service.list_sync_jobs(db, tenant_id, cloud_account_id, limit=limit + 8)
    except Exception:
        logger.exception("copilot list_sync_jobs failed")
        return []
    out: list[dict] = []
    for job in jobs:
        if job.status != "failed":
            continue
        out.append(
            {
                "sync_job_id": str(job.id),
                "job_type": job.job_type,
                "created_at": _iso(job.created_at),
                "completed_at": _iso(job.completed_at),
                "error_message": (job.error_message or "")[:500] or None,
            }
        )
        if len(out) >= limit:
            break
    return out


def _rejected_approval_requests(
    db: Session, organization_id: UUID, tenant_id: UUID, cloud_account_id: UUID, limit: int
) -> list[dict]:
    rows = (
        db.query(ApprovalRequest)
        .filter(
            ApprovalRequest.organization_id == organization_id,
            ApprovalRequest.tenant_id == tenant_id,
            ApprovalRequest.cloud_account_id == cloud_account_id,
            ApprovalRequest.status == "rejected",
        )
        .order_by(desc(ApprovalRequest.updated_at))
        .limit(limit)
        .all()
    )
    out: list[dict] = []
    for req in rows:
        out.append(
            {
                "approval_request_id": str(req.id),
                "recommendation_id": str(req.recommendation_id),
                "status": req.status,
                "rejected_at": _iso(req.rejected_at),
                "summary": approval_request_service.recommendation_summary_for_request(db, req),
            }
        )
    return out


def _failed_execution_signals(db: Session, tenant_id: UUID, cloud_account_id: UUID, limit: int) -> list[dict]:
    rows = (
        db.query(RecommendationOutcome)
        .filter(
            RecommendationOutcome.tenant_id == tenant_id,
            RecommendationOutcome.cloud_account_id == cloud_account_id,
            or_(
                RecommendationOutcome.execution_notes.ilike("%fail%"),
                RecommendationOutcome.impact_status == "regression",
            ),
        )
        .order_by(desc(RecommendationOutcome.updated_at))
        .limit(limit)
        .all()
    )
    out: list[dict] = []
    for row in rows:
        out.append(
            {
                "recommendation_id": str(row.recommendation_id),
                "resource_id": row.resource_id,
                "workflow_status": recommendation_outcome_service.normalized_workflow_status(row.workflow_status),
                "impact_status": row.impact_status,
                "execution_notes_excerpt": (row.execution_notes or "")[:400] or None,
                "updated_at": _iso(row.updated_at),
            }
        )
    return out


def _summary_compact(db: Session, tenant_id: UUID, cloud_account_id: UUID) -> dict | None:
    try:
        s = summary_service.get_cloud_account_summary(db, tenant_id, cloud_account_id)
        top_save = s.top_savings_opportunity
        top_risk = s.top_risk_issue
        return {
            "total_estimated_monthly_savings": s.total_estimated_monthly_savings,
            "total_cost": s.total_cost,
            "savings_percentage": s.savings_percentage,
            "total_recommendations": s.total_recommendations,
            "cost_window_label": s.cost_window_label,
            "cost_period_start": s.cost_period_start,
            "cost_period_end": s.cost_period_end,
            "top_savings_opportunity": (
                {
                    "recommendation_id": str(top_save.recommendation_id),
                    "resource_id": top_save.resource_id,
                    "summary": top_save.summary,
                    "estimated_savings": top_save.estimated_savings,
                    "risk_level": top_save.risk_level,
                }
                if top_save
                else None
            ),
            "top_risk_issue": (
                {
                    "recommendation_id": str(top_risk.recommendation_id),
                    "resource_id": top_risk.resource_id,
                    "summary": top_risk.summary,
                    "risk_level": top_risk.risk_level,
                }
                if top_risk
                else None
            ),
        }
    except Exception as exc:
        logger.info("copilot summary unavailable: %s", exc)
        return {"error": "summary_unavailable", "message": str(exc)[:200]}


def _top_opportunities(db: Session, tenant_id: UUID, cloud_account_id: UUID, limit: int) -> list[dict]:
    try:
        tops = recommendation_service.get_top_opportunities(db, tenant_id, cloud_account_id, limit=limit)
        return [
            {
                "recommendation_id": str(t.recommendation_id),
                "resource_id": t.resource_id,
                "recommendation_type": t.recommendation_type,
                "summary": t.summary,
                "estimated_savings": t.estimated_savings,
                "risk_level": t.risk_level,
                "priority_bucket": t.priority_bucket,
            }
            for t in tops
        ]
    except Exception:
        logger.exception("copilot get_top_opportunities failed")
        return []


def _cost_trends(db: Session, tenant_id: UUID, cloud_account_id: UUID, limit: int) -> list[dict] | dict:
    try:
        raw = trend_service.detect_cost_trends(db, tenant_id, cloud_account_id)
        scored = sorted(raw, key=lambda r: abs(float(r.get("percent_change") or 0)), reverse=True)[:limit]
        return scored
    except Exception as exc:
        logger.info("copilot cost trends unavailable: %s", exc)
        return {"error": "trends_unavailable", "message": str(exc)[:200]}


def _tenant_directory_for_org(db: Session, organization_id: UUID, current_tenant_id: UUID, limit: int) -> list[dict]:
    rows = (
        db.query(Tenant)
        .filter(Tenant.organization_id == organization_id)
        .order_by(Tenant.name.asc())
        .limit(limit)
        .all()
    )
    out: list[dict] = []
    for t in rows:
        cas = db.query(CloudAccount).filter(CloudAccount.tenant_id == t.id).all()
        proof = None
        try:
            proof = recommendation_outcome_service.savings_proof_summary_for_tenant(db, t.id)
        except Exception:
            pass
        bad_conn = sum(1 for c in cas if (c.connection_status or "") != "valid")
        out.append(
            {
                "tenant_id": str(t.id),
                "tenant_name": t.name,
                "cloud_accounts_count": len(cas),
                "cloud_accounts_with_connection_issues": bad_conn,
                "savings_proof_tenant": proof,
                "is_current_tenant": str(t.id) == str(current_tenant_id),
            }
        )
    return out


def build_scoped_copilot_context(
    db: Session,
    *,
    intent: str,
    organization_id: UUID,
    tenant_id: UUID,
    cloud_account_id: UUID,
    effective_operational_access: str,
    org_membership_role: str,
) -> dict:
    """
    Return only the platform_data subset relevant to ``intent`` (plus meta.intent).
    """
    resolved_intent = intent if intent in COPILOT_INTENTS else "general_summary"

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    cloud = (
        db.query(CloudAccount)
        .filter(CloudAccount.id == cloud_account_id, CloudAccount.tenant_id == tenant_id)
        .first()
    )
    scope = _scope_block(
        organization_id=organization_id,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        effective_operational_access=effective_operational_access,
        org_membership_role=org_membership_role,
        tenant=tenant,
        cloud=cloud,
    )

    payload: dict = {"intent": resolved_intent, "scope": scope}

    if resolved_intent == "prioritize":
        payload["connection"] = _connection_block(cloud)
        from app.services import attention_tasks_service

        payload["scored_attention_top"] = attention_tasks_service.score_account_attention_items(
            db, organization_id, tenant_id, cloud_account_id, limit=24
        )[:12]

    elif resolved_intent == "blockers":
        payload["connection"] = _connection_block(cloud)
        from app.services import attention_tasks_service

        payload["scored_attention_top"] = attention_tasks_service.score_account_blockers_subset(
            db, organization_id, tenant_id, cloud_account_id, limit=12
        )

    elif resolved_intent == "low_priority":
        payload["connection"] = _connection_block(cloud)
        # Full evaluation happens in copilot_low_priority_service (DB); keep payload tiny.

    elif resolved_intent == "approvals":
        items = _pending_approvals_list(db, organization_id, tenant_id, cloud_account_id, 15)
        _enrich_approvals_preflight_dryrun(db, tenant_id, cloud_account_id, items)
        payload["pending_approvals"] = items

    elif resolved_intent == "executions":
        items = _ready_to_execute_list(db, tenant_id, cloud_account_id, 12)
        _enrich_execution_eligibility(db, organization_id, tenant_id, cloud_account_id, items)
        payload["ready_to_execute"] = items
        blocked = []
        for row in items:
            ee = row.get("execution_eligibility") or {}
            if not ee.get("execution_eligible"):
                blocked.append(
                    {
                        "recommendation_id": row.get("recommendation_id"),
                        "summary": row.get("summary"),
                        "blocking_reason": ee.get("blocking_reason"),
                        "display_label": ee.get("display_label"),
                    }
                )
        payload["not_ready_to_execute"] = blocked[:10]

    elif resolved_intent == "savings":
        payload["summary"] = _summary_compact(db, tenant_id, cloud_account_id)
        try:
            payload["savings_proof_account"] = recommendation_outcome_service.savings_proof_summary_for_cloud_account(
                db, tenant_id, cloud_account_id
            )
        except Exception:
            payload["savings_proof_account"] = None
        try:
            payload["savings_proof_tenant"] = recommendation_outcome_service.savings_proof_summary_for_tenant(
                db, tenant_id
            )
        except Exception:
            payload["savings_proof_tenant"] = None
        payload["cost_trends"] = _cost_trends(db, tenant_id, cloud_account_id, 6)
        act = (
            db.query(RecommendationOutcome)
            .filter(
                RecommendationOutcome.tenant_id == tenant_id,
                RecommendationOutcome.cloud_account_id == cloud_account_id,
                RecommendationOutcome.estimated_savings.isnot(None),
            )
            .order_by(desc(RecommendationOutcome.applied_at), desc(RecommendationOutcome.updated_at))
            .limit(8)
            .all()
        )
        payload["recent_proof_outcomes"] = [
            {
                "recommendation_id": str(o.recommendation_id),
                "before_cost": float(o.before_cost) if o.before_cost is not None else None,
                "after_cost": float(o.after_cost) if o.after_cost is not None else None,
                "estimated_savings": float(o.estimated_savings) if o.estimated_savings is not None else None,
                "applied_at": _iso(o.applied_at),
            }
            for o in act
        ]

    elif resolved_intent == "tenants":
        payload["tenant_directory"] = _tenant_directory_for_org(db, organization_id, tenant_id, 20)
        payload["current_cloud_summary"] = _summary_compact(db, tenant_id, cloud_account_id)
        payload["connection"] = _connection_block(cloud)
        payload["top_opportunities_current_account"] = _top_opportunities(db, tenant_id, cloud_account_id, 5)
        payload["failed_sync_jobs_current_account"] = _failed_sync_jobs(db, tenant_id, cloud_account_id, 5)

    else:  # general_summary
        payload["connection"] = _connection_block(cloud)
        payload["summary"] = _summary_compact(db, tenant_id, cloud_account_id)
        payload["top_opportunities"] = _top_opportunities(db, tenant_id, cloud_account_id, 4)
        try:
            recs = recommendation_service.list_recommendations(db, tenant_id, cloud_account_id, latest_only=True)
            payload["recommendation_count"] = len(recs)
        except Exception:
            payload["recommendation_count"] = None

    return payload


def build_copilot_data_bundle(
    db: Session,
    *,
    organization_id: UUID,
    tenant_id: UUID,
    cloud_account_id: UUID,
    effective_operational_access: str,
    org_membership_role: str,
) -> dict:
    """Backward-compatible full bundle (e.g. tests); prefer build_scoped_copilot_context."""
    intent = "general_summary"
    return build_scoped_copilot_context(
        db,
        intent=intent,
        organization_id=organization_id,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        effective_operational_access=effective_operational_access,
        org_membership_role=org_membership_role,
    )


def run_intent_and_scoped_context(
    db: Session,
    *,
    query: str,
    organization_id: UUID,
    tenant_id: UUID,
    cloud_account_id: UUID,
    effective_operational_access: str,
    org_membership_role: str,
) -> tuple[str, dict]:
    intent = classify_copilot_intent(query)
    scoped = build_scoped_copilot_context(
        db,
        intent=intent,
        organization_id=organization_id,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        effective_operational_access=effective_operational_access,
        org_membership_role=org_membership_role,
    )
    return intent, scoped
