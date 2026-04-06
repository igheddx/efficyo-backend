"""Cross-tenant pending approvals for an organization (approver workflow)."""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.cloud_account import CloudAccount
from app.models.organization import Organization
from app.models.recommendation import Recommendation
from app.models.recommendation_outcome import RecommendationOutcome
from app.models.tenant import Tenant
from app.schemas.approvals import PendingApprovalItemRead, PendingApprovalsPageRead
from app.services import preflight_dry_run_service, recommendation_outcome_service, recommendation_service
from app.services.recommendation_credibility import rank_by_computed_score, why_it_matters_for

logger = logging.getLogger(__name__)

_PREVIEW_TYPES = frozenset({"s3_enable_public_access_block", "s3_add_required_tags"})


def _is_pending(outcome: RecommendationOutcome | None) -> bool:
    if outcome is None:
        return True
    ws = recommendation_outcome_service.normalized_workflow_status(outcome.workflow_status)
    return ws == "suggested"


def _preview_payload(
    db: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
    recommendation_type: str,
    *,
    include_previews: bool,
) -> tuple[str | None, str | None, list | None]:
    if not include_previews:
        return None, None, None
    rt = (recommendation_type or "").lower()
    if rt not in _PREVIEW_TYPES:
        return None, None, None
    try:
        pf = preflight_dry_run_service.run_preflight(
            db, tenant_id, cloud_account_id, recommendation_id
        )
        checks = pf.get("checks") if isinstance(pf.get("checks"), list) else None
        status = pf.get("status") if isinstance(pf.get("status"), str) else None
    except Exception:
        logger.debug("Preflight failed for pending approval preview", exc_info=True)
        return "unavailable", None, None
    try:
        dr = preflight_dry_run_service.run_dry_run(
            db, tenant_id, cloud_account_id, recommendation_id, tag_values=None
        )
        impact = dr.get("impact_summary") if isinstance(dr.get("impact_summary"), str) else None
    except Exception:
        logger.debug("Dry-run failed for pending approval preview", exc_info=True)
        impact = None
    return status, impact, checks


def count_pending_approvals_for_user(db: Session, organization_id: UUID, user_id: UUID | None) -> int:
    """Count ApprovalAssignment rows where this user is an assignee with pending status.

    This is the badge count — items the current user personally needs to act on.
    """
    if user_id is None:
        return 0
    from app.models.approval_request import ApprovalAssignment, ApprovalRequest
    return (
        db.query(ApprovalAssignment)
        .join(ApprovalRequest, ApprovalRequest.id == ApprovalAssignment.approval_request_id)
        .filter(
            ApprovalRequest.organization_id == organization_id,
            ApprovalRequest.status.in_(["submitted", "partially_approved"]),
            ApprovalAssignment.approver_user_id == user_id,
            ApprovalAssignment.status == "pending",
        )
        .count()
    )


def count_pending_approvals_for_organization(db: Session, organization_id: UUID) -> int:
    """Count ALL pending items for the org (used by the approvals queue page total)."""
    tenant_ids = [
        tid
        for (tid,) in db.query(Tenant.id).filter(Tenant.organization_id == organization_id).all()
    ]
    n = 0
    for tenant_id in tenant_ids:
        pairs = (
            db.query(Recommendation.cloud_account_id)
            .filter(Recommendation.tenant_id == tenant_id)
            .distinct()
            .all()
        )
        for (cloud_account_id,) in pairs:
            recs = recommendation_service.list_recommendations(
                db_session=db,
                tenant_id=tenant_id,
                cloud_account_id=cloud_account_id,
                latest_only=True,
            )
            if not recs:
                continue
            outcome_rows = (
                db.query(RecommendationOutcome)
                .filter(
                    RecommendationOutcome.tenant_id == tenant_id,
                    RecommendationOutcome.cloud_account_id == cloud_account_id,
                )
                .all()
            )
            outcome_by_id = {o.recommendation_id: o for o in outcome_rows}
            for rec in recs:
                if _is_pending(outcome_by_id.get(rec.id)):
                    n += 1
    return n


def list_pending_approvals_for_organization(
    db: Session,
    organization_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
    include_previews: bool = False,
) -> PendingApprovalsPageRead:
    tenant_rows = (
        db.query(Tenant.id, Tenant.name)
        .filter(Tenant.organization_id == organization_id)
        .all()
    )
    if not tenant_rows:
        return PendingApprovalsPageRead(items=[], total=0)

    tenant_id_to_name = {tid: name or "" for tid, name in tenant_rows}
    tenant_ids = list(tenant_id_to_name.keys())

    org = db.query(Organization).filter(Organization.id == organization_id).first()
    org_name = org.name if org is not None else ""

    items_flat: list[PendingApprovalItemRead] = []

    for tenant_id in tenant_ids:
        pairs = (
            db.query(Recommendation.cloud_account_id)
            .filter(Recommendation.tenant_id == tenant_id)
            .distinct()
            .all()
        )
        for (cloud_account_id,) in pairs:
            recs = recommendation_service.list_recommendations(
                db_session=db,
                tenant_id=tenant_id,
                cloud_account_id=cloud_account_id,
                latest_only=True,
            )
            if not recs:
                continue
            ranks = rank_by_computed_score(recs)
            outcome_rows = (
                db.query(RecommendationOutcome)
                .filter(
                    RecommendationOutcome.tenant_id == tenant_id,
                    RecommendationOutcome.cloud_account_id == cloud_account_id,
                )
                .all()
            )
            outcome_by_id = {o.recommendation_id: o for o in outcome_rows}

            ca = (
                db.query(CloudAccount)
                .filter(CloudAccount.id == cloud_account_id, CloudAccount.tenant_id == tenant_id)
                .first()
            )
            ca_name = ca.name if ca is not None else ""
            ca_aws = ca.account_id if ca is not None else None

            for rec in recs:
                out = outcome_by_id.get(rec.id)
                if not _is_pending(out):
                    continue
                why = why_it_matters_for(ranks.get(rec.id), rec.recommendation_category)
                pf_status, impact, checks = _preview_payload(
                    db,
                    tenant_id,
                    cloud_account_id,
                    rec.id,
                    rec.recommendation_type,
                    include_previews=include_previews,
                )
                items_flat.append(
                    PendingApprovalItemRead(
                        recommendation_id=rec.id,
                        tenant_id=tenant_id,
                        tenant_name=tenant_id_to_name.get(tenant_id, ""),
                        cloud_account_id=cloud_account_id,
                        cloud_account_name=ca_name,
                        cloud_account_aws_id=ca_aws,
                        organization_id=organization_id,
                        organization_name=org_name,
                        summary=rec.summary,
                        recommendation_category=rec.recommendation_category,
                        recommendation_type=rec.recommendation_type,
                        resource_id=rec.resource_id,
                        resource_type=rec.resource_type,
                        estimated_savings=(
                            float(rec.estimated_savings)
                            if rec.estimated_savings is not None
                            else None
                        ),
                        risk_level=rec.risk_level,
                        why_it_matters=why,
                        workflow_status="suggested",
                        preflight_status=pf_status,
                        dry_run_impact_summary=impact,
                        preflight_checks=checks,
                    )
                )

    total = len(items_flat)
    slice_rows = items_flat[offset : offset + limit]
    return PendingApprovalsPageRead(items=slice_rows, total=total)
