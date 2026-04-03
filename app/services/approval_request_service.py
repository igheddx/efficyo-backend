"""Multi-approver approval requests (all assignees must approve)."""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.db import utc_now
from app.services.access_resolution_service import (
    access_rank,
    org_membership_role_label,
    resolve_effective_access_for_user,
)
from app.models.approval_request import ApprovalAssignment, ApprovalRequest
from app.models.cloud_account import CloudAccount
from app.models.execution_owner import ExecutionOwnerAssignment
from app.models.organization import OrgMembership
from app.models.recommendation import Recommendation
from app.models.tenant import Tenant
from app.models.user import User
from app.services import recommendation_outcome_service
from app.services import tag_values_service
from app.services.execution_policy_service import resolve_execution_policy
from app.services.recommendation_type_utils import is_add_required_tags_recommendation

logger = logging.getLogger(__name__)

OPEN_STATUSES = frozenset({"submitted", "partially_approved"})


def can_submit_multi_approval(role: str, is_platform_root: bool) -> bool:
    """Deprecated: use access_resolution_service.can_submit_multi_approval_for_context."""
    if is_platform_root:
        return True
    return role in {"admin", "org_admin"}


def assignee_may_approve_tenant_account(
    db: Session,
    *,
    organization_id: UUID,
    tenant_id: UUID,
    cloud_account_id: UUID,
    assignee_user_id: UUID,
    assignee_membership_role: str,
) -> bool:
    eff = resolve_effective_access_for_user(
        db,
        user_id=assignee_user_id,
        organization_id=organization_id,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        membership_role=assignee_membership_role,
    )
    return access_rank(eff) >= access_rank("approver")


def list_eligible_approvers_for_account(
    db: Session,
    *,
    organization_id: UUID,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> list[dict]:
    """
    Org members who may be assigned as approvers for this tenant/cloud account
    (effective operational access is approver or admin).
    """
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant is None or tenant.organization_id != organization_id:
        raise ValueError("tenant_org_mismatch")
    ca = (
        db.query(CloudAccount)
        .filter(CloudAccount.id == cloud_account_id, CloudAccount.tenant_id == tenant_id)
        .first()
    )
    if ca is None:
        raise ValueError("cloud_account_not_found")

    rows = (
        db.query(OrgMembership, User)
        .join(User, User.id == OrgMembership.user_id)
        .filter(OrgMembership.organization_id == organization_id)
        .filter(OrgMembership.user_id.isnot(None))
        .order_by(User.email.asc())
        .all()
    )
    out: list[dict] = []
    for mem, user in rows:
        uid = user.id
        mrole = mem.role or ""
        if not assignee_may_approve_tenant_account(
            db,
            organization_id=organization_id,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            assignee_user_id=uid,
            assignee_membership_role=mrole,
        ):
            continue
        eff = resolve_effective_access_for_user(
            db,
            user_id=uid,
            organization_id=organization_id,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            membership_role=mrole,
        )
        out.append(
            {
                "id": mem.id,
                "organization_id": organization_id,
                "user_id": uid,
                "email": user.email,
                "display_name": user.display_name,
                "role": mem.role,
                "created_at": mem.created_at,
                "updated_at": mem.updated_at,
                "effective_access_role": eff,
            }
        )
    return out


def _latest_request(
    db: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
) -> ApprovalRequest | None:
    return (
        db.query(ApprovalRequest)
        .filter(
            ApprovalRequest.tenant_id == tenant_id,
            ApprovalRequest.cloud_account_id == cloud_account_id,
            ApprovalRequest.recommendation_id == recommendation_id,
        )
        .order_by(desc(ApprovalRequest.created_at))
        .first()
    )


def has_open_multi_approver_request(
    db: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
) -> bool:
    row = _latest_request(db, tenant_id, cloud_account_id, recommendation_id)
    return row is not None and row.status in OPEN_STATUSES


def execution_blocked_by_approval_request(
    db: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
) -> tuple[bool, str | None]:
    """
    If a multi-approver request exists for this recommendation, execution is allowed only
    when the latest request is fully approved. No request row => not blocked here (legacy path).
    """
    row = _latest_request(db, tenant_id, cloud_account_id, recommendation_id)
    if row is None:
        return False, None
    if row.status == "approved":
        return False, None
    if row.status == "rejected":
        return True, "approval_request_rejected"
    return True, "approval_request_pending"


def _progress_counts(req: ApprovalRequest) -> tuple[int, int]:
    assigns = list(req.assignments or [])
    required = len(assigns)
    done = sum(1 for a in assigns if a.status == "approved")
    return done, required


def request_to_read(req: ApprovalRequest, *, include_assignments: bool = False) -> dict:
    done, required = _progress_counts(req)
    base = {
        "id": req.id,
        "organization_id": req.organization_id,
        "tenant_id": req.tenant_id,
        "cloud_account_id": req.cloud_account_id,
        "recommendation_id": req.recommendation_id,
        "submitted_by": req.submitted_by,
        "submitted_by_role": req.submitted_by_role,
        "approval_mode": req.approval_mode,
        "status": req.status,
        "submitted_at": req.submitted_at,
        "approved_at": req.approved_at,
        "rejected_at": req.rejected_at,
        "created_at": req.created_at,
        "updated_at": req.updated_at,
        "approvals_complete": done,
        "approvals_required": required,
        "execution_owner_user_id": None,
        "execution_owner_name": None,
        "execution_owner_role": None,
        "tag_values": dict(req.requested_tag_values_json or {}) if req.requested_tag_values_json else None,
    }
    owner = (
        req.execution_owner_assignments[0]
        if getattr(req, "execution_owner_assignments", None)
        else None
    )
    if owner is not None:
        base["execution_owner_user_id"] = owner.owner_user_id
        base["execution_owner_name"] = owner.owner_name_snapshot
        base["execution_owner_role"] = owner.owner_role_snapshot
    if include_assignments:
        base["assignments"] = list(req.assignments or [])
    return base


def create_approval_request(
    db: Session,
    *,
    organization_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
    approver_user_ids: list[UUID],
    execution_owner_user_id: UUID,
    approval_mode: str,
    tag_values: dict[str, str] | None,
    submitted_by: str | None,
    submitted_by_role: str | None,
) -> ApprovalRequest:
    if approval_mode != "all_required":
        raise ValueError("unsupported_approval_mode")

    ca = (
        db.query(CloudAccount)
        .filter(CloudAccount.id == cloud_account_id, CloudAccount.tenant_id.isnot(None))
        .first()
    )
    if ca is None:
        raise ValueError("cloud_account_not_found")
    tenant_id = ca.tenant_id

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant is None or tenant.organization_id != organization_id:
        raise ValueError("organization_mismatch")

    rec = (
        db.query(Recommendation)
        .filter(
            Recommendation.id == recommendation_id,
            Recommendation.tenant_id == tenant_id,
            Recommendation.cloud_account_id == cloud_account_id,
        )
        .first()
    )
    if rec is None:
        raise ValueError("recommendation_not_found")

    outcome = recommendation_outcome_service.create_outcome_for_recommendation(
        db_session=db,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        recommendation_id=recommendation_id,
    )
    ws = recommendation_outcome_service.normalized_workflow_status(outcome.workflow_status)
    if ws not in {"suggested", "rejected"}:
        raise ValueError("recommendation_not_pending")

    if has_open_multi_approver_request(db, tenant_id, cloud_account_id, recommendation_id):
        raise ValueError("open_approval_request_exists")

    seen: set[UUID] = set()
    unique_approvers: list[UUID] = []
    for uid in approver_user_ids:
        if uid in seen:
            continue
        seen.add(uid)
        unique_approvers.append(uid)
    if not unique_approvers:
        raise ValueError("no_approvers")
    for uid in unique_approvers:
        m = (
            db.query(OrgMembership, User)
            .join(User, User.id == OrgMembership.user_id)
            .filter(
                OrgMembership.organization_id == organization_id,
                OrgMembership.user_id == uid,
            )
            .first()
        )
        if m is None:
            raise ValueError("approver_not_in_org")
        _mem, user = m
        if not assignee_may_approve_tenant_account(
            db,
            organization_id=organization_id,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            assignee_user_id=uid,
            assignee_membership_role=_mem.role or "",
        ):
            raise ValueError("approver_ineligible_role")
    owner_member_row = (
        db.query(OrgMembership, User)
        .join(User, User.id == OrgMembership.user_id)
        .filter(
            OrgMembership.organization_id == organization_id,
            OrgMembership.user_id == execution_owner_user_id,
        )
        .first()
    )
    if owner_member_row is None:
        raise ValueError("execution_owner_not_in_org")
    owner_mem, owner_user = owner_member_row
    owner_eff = resolve_effective_access_for_user(
        db,
        user_id=execution_owner_user_id,
        organization_id=organization_id,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        membership_role=owner_mem.role or "",
    )
    if access_rank(owner_eff) < access_rank("approver") and (owner_mem.role or "").strip().lower() != "owner":
        raise ValueError("execution_owner_ineligible_role")

    validated_tag_values = tag_values_service.validate_tag_values(tag_values)
    policy = resolve_execution_policy(
        db,
        organization_id=organization_id,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        recommendation_type=rec.recommendation_type,
        recommendation_risk_level=rec.risk_level,
    )
    requires_saved_tags = is_add_required_tags_recommendation(rec.recommendation_type) and policy.execution_mode in {
        "approved_then_manual",
        "approved_then_auto_allowed",
    }
    if requires_saved_tags and not validated_tag_values:
        raise ValueError("tag_values_required_for_tag_recommendation")

    now = utc_now()
    req = ApprovalRequest(
        organization_id=organization_id,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        recommendation_id=recommendation_id,
        submitted_by=submitted_by,
        submitted_by_role=submitted_by_role,
        requested_tag_values_json=validated_tag_values or None,
        approval_mode=approval_mode,
        status="submitted",
        submitted_at=now,
    )
    db.add(req)
    db.flush()

    for uid in unique_approvers:
        m = (
            db.query(OrgMembership, User)
            .join(User, User.id == OrgMembership.user_id)
            .filter(
                OrgMembership.organization_id == organization_id,
                OrgMembership.user_id == uid,
            )
            .first()
        )
        assert m is not None
        mem, user = m
        db.add(
            ApprovalAssignment(
                approval_request_id=req.id,
                approver_user_id=uid,
                approver_name_snapshot=user.display_name or user.email or str(uid),
                approver_role_snapshot=mem.role or "approver",
                status="pending",
            )
        )
    db.add(
        ExecutionOwnerAssignment(
            organization_id=organization_id,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            recommendation_id=recommendation_id,
            approval_request_id=req.id,
            owner_user_id=execution_owner_user_id,
            owner_name_snapshot=owner_user.display_name or owner_user.email or str(execution_owner_user_id),
            owner_role_snapshot=owner_mem.role or "approver",
            assigned_by=submitted_by,
            assigned_by_role=submitted_by_role,
            assigned_at=now,
        )
    )

    if validated_tag_values:
        outcome.tag_values_json = validated_tag_values
        tag_values_service.upsert_account_tag_keys(
            db, cloud_account_id=cloud_account_id, keys=list(validated_tag_values.keys())
        )

    db.commit()
    db.refresh(req)
    return req


def recommendation_summary_for_request(db: Session, req: ApprovalRequest) -> str | None:
    row = (
        db.query(Recommendation.summary)
        .filter(
            Recommendation.id == req.recommendation_id,
            Recommendation.tenant_id == req.tenant_id,
            Recommendation.cloud_account_id == req.cloud_account_id,
        )
        .first()
    )
    return (row[0] or None) if row else None


def list_approval_requests(
    db: Session,
    *,
    organization_id: UUID,
    user_id: UUID | None,
    recommendation_id: UUID | None,
    status_filter: str | None,
    pending_for_me: bool,
    limit: int,
    offset: int,
    admin_view: bool,
) -> tuple[list[ApprovalRequest], int]:
    q = db.query(ApprovalRequest).filter(ApprovalRequest.organization_id == organization_id)
    if recommendation_id is not None:
        q = q.filter(ApprovalRequest.recommendation_id == recommendation_id)
    if status_filter:
        q = q.filter(ApprovalRequest.status == status_filter)
    if pending_for_me and user_id is not None:
        q = (
            q.join(ApprovalAssignment, ApprovalAssignment.approval_request_id == ApprovalRequest.id)
            .filter(
                ApprovalAssignment.approver_user_id == user_id,
                ApprovalAssignment.status == "pending",
            )
            .distinct()
        )
    elif not admin_view and user_id is not None:
        q = (
            q.join(ApprovalAssignment, ApprovalAssignment.approval_request_id == ApprovalRequest.id)
            .filter(ApprovalAssignment.approver_user_id == user_id)
            .distinct()
        )

    total = q.count()
    rows = (
        q.order_by(desc(ApprovalRequest.created_at))
        .offset(offset)
        .limit(limit)
        .all()
    )
    for r in rows:
        _ = r.assignments  # preload
    return rows, total


def get_approval_request(
    db: Session,
    *,
    request_id: UUID,
    organization_id: UUID,
) -> ApprovalRequest | None:
    row = (
        db.query(ApprovalRequest)
        .filter(
            ApprovalRequest.id == request_id,
            ApprovalRequest.organization_id == organization_id,
        )
        .first()
    )
    if row is None:
        return None
    _ = row.assignments
    return row


def user_may_view_request(db: Session, req: ApprovalRequest, user_id: UUID | None, admin_view: bool) -> bool:
    if admin_view:
        return True
    if user_id is None:
        return False
    return (
        db.query(ApprovalAssignment.id)
        .filter(
            ApprovalAssignment.approval_request_id == req.id,
            ApprovalAssignment.approver_user_id == user_id,
        )
        .first()
        is not None
    )


def approve_assignment(
    db: Session,
    *,
    request_id: UUID,
    organization_id: UUID,
    actor_user_id: UUID,
    actor_email: str | None,
    actor_role: str,
    comment: str | None,
    actor_is_platform_root: bool = False,
) -> ApprovalRequest:
    req = get_approval_request(db, request_id=request_id, organization_id=organization_id)
    if req is None:
        raise ValueError("approval_request_not_found")
    if req.status not in OPEN_STATUSES:
        raise ValueError("approval_request_not_actionable")

    eff = resolve_effective_access_for_user(
        db,
        user_id=actor_user_id,
        organization_id=organization_id,
        tenant_id=req.tenant_id,
        cloud_account_id=req.cloud_account_id,
        membership_role=actor_role,
        is_platform_root=actor_is_platform_root,
    )
    if access_rank(eff) < access_rank("approver"):
        raise ValueError("forbidden")

    a = (
        db.query(ApprovalAssignment)
        .filter(
            ApprovalAssignment.approval_request_id == req.id,
            ApprovalAssignment.approver_user_id == actor_user_id,
        )
        .first()
    )
    if a is None:
        raise ValueError("not_assigned_approver")
    if a.status != "pending":
        raise ValueError("assignment_already_acted")

    now = utc_now()
    a.status = "approved"
    a.acted_at = now
    if comment is not None:
        t = comment.strip()
        a.comment = t if t else None
    db.add(a)

    assigns = list(req.assignments or [])
    all_approved = all(x.status == "approved" for x in assigns)
    if all_approved:
        req.status = "approved"
        req.approved_at = now
        req.updated_at = now
        db.add(req)
        db.flush()
        last_email = actor_email or a.approver_name_snapshot
        try:
            recommendation_outcome_service.approve_recommendation(
                db_session=db,
                tenant_id=req.tenant_id,
                cloud_account_id=req.cloud_account_id,
                recommendation_id=req.recommendation_id,
                approved_by=last_email,
                approved_role=eff,
                approval_comment=f"Multi-approver request {req.id} completed.",
                approved_membership_role=org_membership_role_label(actor_role),
                approved_access_role=eff,
            )
        except ValueError as exc:
            if str(exc) != "recommendation_not_found":
                raise
        try:
            from app.services import bulk_tagging_service

            bulk_tagging_service.on_approval_request_approved(
                db,
                approval_request_id=req.id,
                approved_by=last_email,
                approved_role=eff,
                approved_membership_role=org_membership_role_label(actor_role),
                approved_access_role=eff,
            )
        except Exception:
            logger.debug("bulk tagging approval hook skipped", exc_info=True)
    else:
        req.status = "partially_approved"
        req.updated_at = now
        db.add(req)

    db.commit()
    db.refresh(req)
    return req


def reject_assignment(
    db: Session,
    *,
    request_id: UUID,
    organization_id: UUID,
    actor_user_id: UUID,
    actor_email: str | None,
    actor_role: str,
    comment: str | None,
    actor_is_platform_root: bool = False,
) -> ApprovalRequest:
    req = get_approval_request(db, request_id=request_id, organization_id=organization_id)
    if req is None:
        raise ValueError("approval_request_not_found")
    if req.status not in OPEN_STATUSES:
        raise ValueError("approval_request_not_actionable")

    eff = resolve_effective_access_for_user(
        db,
        user_id=actor_user_id,
        organization_id=organization_id,
        tenant_id=req.tenant_id,
        cloud_account_id=req.cloud_account_id,
        membership_role=actor_role,
        is_platform_root=actor_is_platform_root,
    )
    if access_rank(eff) < access_rank("approver"):
        raise ValueError("forbidden")

    a = (
        db.query(ApprovalAssignment)
        .filter(
            ApprovalAssignment.approval_request_id == req.id,
            ApprovalAssignment.approver_user_id == actor_user_id,
        )
        .first()
    )
    if a is None:
        raise ValueError("not_assigned_approver")
    if a.status != "pending":
        raise ValueError("assignment_already_acted")

    reason = (comment or "").strip() or "Rejected by approver."

    now = utc_now()
    a.status = "rejected"
    a.acted_at = now
    a.comment = reason[:4000]
    db.add(a)

    req.status = "rejected"
    req.rejected_at = now
    req.updated_at = now
    db.add(req)
    db.flush()

    try:
        recommendation_outcome_service.reject_recommendation(
            db_session=db,
            tenant_id=req.tenant_id,
            cloud_account_id=req.cloud_account_id,
            recommendation_id=req.recommendation_id,
            rejection_reason=reason,
            rejected_by=actor_email,
        )
    except ValueError as exc:
        err = str(exc)
        if err not in {"recommendation_not_found", "cannot_reject_applied_or_verified"}:
            raise
    try:
        from app.services import bulk_tagging_service

        bulk_tagging_service.on_approval_request_rejected(
            db,
            approval_request_id=req.id,
            reason=reason,
        )
    except Exception:
        logger.debug("bulk tagging reject hook skipped", exc_info=True)

    db.commit()
    db.refresh(req)
    return req
