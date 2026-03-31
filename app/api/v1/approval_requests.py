from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.user_context import UserContext, get_user_context
from app.schemas.approval_request import (
    ApprovalAssignmentRead,
    ApprovalDecisionBody,
    ApprovalRequestCreate,
    ApprovalRequestDetailRead,
    ApprovalRequestRead,
    EligibleApproverRead,
)
from app.models.cloud_account import CloudAccount
from app.services import (
    access_resolution_service,
    approval_request_service,
    notification_service,
    tenant_scope_service,
)

router = APIRouter(prefix="/approval-requests", tags=["approval-requests"])


def _resolve_user_id(db: Session, ctx: UserContext) -> UUID:
    uid = notification_service.resolve_notification_user_id(db, ctx.user_id, ctx.email)
    if uid is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This action requires a resolved user identity.",
        )
    return uid


def _admin_list_view(db_session: Session, ctx: UserContext, org_id) -> bool:
    return access_resolution_service.user_may_admin_view_approval_requests(db_session, ctx, org_id)


def _can_list(db_session: Session, ctx: UserContext, org_id) -> bool:
    return access_resolution_service.user_may_list_org_approval_requests(db_session, ctx, org_id)


def _to_read(db_session: Session, req) -> ApprovalRequestRead:
    d = approval_request_service.request_to_read(req, include_assignments=False)
    d["recommendation_summary"] = approval_request_service.recommendation_summary_for_request(db_session, req)
    return ApprovalRequestRead.model_validate(d)


def _to_detail(db_session: Session, req) -> ApprovalRequestDetailRead:
    d = approval_request_service.request_to_read(req, include_assignments=True)
    assigns = d.pop("assignments", [])
    d["recommendation_summary"] = approval_request_service.recommendation_summary_for_request(db_session, req)
    return ApprovalRequestDetailRead(
        **d,
        assignments=[ApprovalAssignmentRead.model_validate(a) for a in assigns],
    )


@router.post("", response_model=ApprovalRequestDetailRead, status_code=status.HTTP_201_CREATED)
def create_approval_request_endpoint(
    body: ApprovalRequestCreate,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> ApprovalRequestDetailRead:
    tenant_scope_service.assert_organization_accessible(db_session, ctx, body.organization_id)
    org_ctx = tenant_scope_service.require_data_access_organization_id(db_session, ctx)
    if org_ctx != body.organization_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="organization_id must match your current organization.",
        )

    ca = (
        db_session.query(CloudAccount)
        .filter(CloudAccount.id == body.cloud_account_id)
        .first()
    )
    if ca is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found.")
    if not access_resolution_service.can_submit_multi_approval_for_context(
        db_session,
        ctx,
        tenant_id=ca.tenant_id,
        cloud_account_id=body.cloud_account_id,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Approver or admin access on this customer and cloud account is required to submit for approval.",
        )

    try:
        req = approval_request_service.create_approval_request(
            db_session,
            organization_id=body.organization_id,
            cloud_account_id=body.cloud_account_id,
            recommendation_id=body.recommendation_id,
            approver_user_ids=body.approver_user_ids,
            execution_owner_user_id=body.execution_owner_user_id,
            approval_mode=body.approval_mode,
            tag_values=body.tag_values,
            submitted_by=ctx.email,
            submitted_by_role=ctx.role,
        )
    except ValueError as exc:
        msg = str(exc)
        mapping = {
            "cloud_account_not_found": (status.HTTP_404_NOT_FOUND, "Cloud account not found."),
            "organization_mismatch": (status.HTTP_400_BAD_REQUEST, "Cloud account is not in that organization."),
            "recommendation_not_found": (status.HTTP_404_NOT_FOUND, "Recommendation not found."),
            "open_approval_request_exists": (
                status.HTTP_409_CONFLICT,
                "An open approval request already exists for this recommendation.",
            ),
            "no_approvers": (status.HTTP_400_BAD_REQUEST, "Select at least one approver."),
            "approver_not_in_org": (status.HTTP_400_BAD_REQUEST, "An approver is not a member of this organization."),
            "approver_ineligible_role": (
                status.HTTP_400_BAD_REQUEST,
                "Each approver must have approver access (or higher) on this tenant and cloud account.",
            ),
            "execution_owner_not_in_org": (
                status.HTTP_400_BAD_REQUEST,
                "Execution owner must be a member of this organization.",
            ),
            "execution_owner_ineligible_role": (
                status.HTTP_400_BAD_REQUEST,
                "Execution owner must have owner-capable access (admin, approver, or owner).",
            ),
            "tag_values_required_for_tag_recommendation": (
                status.HTTP_400_BAD_REQUEST,
                "Save required tag key/value pairs before submitting this recommendation for approval.",
            ),
            "duplicate_tag_key": (status.HTTP_400_BAD_REQUEST, "Duplicate tag key is not allowed."),
            "tag_key_required": (status.HTTP_400_BAD_REQUEST, "Tag key is required."),
            "tag_value_required": (status.HTTP_400_BAD_REQUEST, "Tag value is required."),
            "tag_key_too_long": (status.HTTP_400_BAD_REQUEST, "Tag key must be 128 characters or fewer."),
            "tag_value_too_long": (status.HTTP_400_BAD_REQUEST, "Tag value must be 256 characters or fewer."),
            "tag_key_invalid_chars": (
                status.HTTP_400_BAD_REQUEST,
                "Tag key contains invalid characters.",
            ),
            "tag_value_invalid_chars": (
                status.HTTP_400_BAD_REQUEST,
                "Tag value contains invalid characters.",
            ),
            "unsupported_approval_mode": (status.HTTP_400_BAD_REQUEST, "Only all_required is supported."),
            "recommendation_not_pending": (
                status.HTTP_409_CONFLICT,
                "Submit for approval only when the recommendation is pending or was rejected.",
            ),
        }
        if msg in mapping:
            code, detail = mapping[msg]
            raise HTTPException(status_code=code, detail=detail) from exc
        raise
    return _to_detail(db_session, req)


@router.get("/eligible-approvers", response_model=list[EligibleApproverRead])
def list_eligible_approvers_endpoint(
    tenant_id: UUID = Query(..., description="Customer tenant for the recommendation / cloud account."),
    cloud_account_id: UUID = Query(...),
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> list[EligibleApproverRead]:
    """
    Users who may be selected as approvers for multi-step approval on this tenant/account.
    Excludes viewers and others without approver-level operational access.
    """
    org_id = tenant_scope_service.require_data_access_organization_id(db_session, ctx)
    tenant_scope_service.require_tenant_accessible(db_session, ctx, tenant_id)
    if not access_resolution_service.can_submit_multi_approval_for_context(
        db_session,
        ctx,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not allowed to load approvers for this context.",
        )
    try:
        rows = approval_request_service.list_eligible_approvers_for_account(
            db_session,
            organization_id=org_id,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
        )
    except ValueError as exc:
        msg = str(exc)
        if msg == "tenant_org_mismatch":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tenant is not in your current organization.",
            ) from exc
        if msg == "cloud_account_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud account not found.") from exc
        raise
    return [EligibleApproverRead.model_validate(r) for r in rows]


@router.get("", response_model=dict)
def list_approval_requests_endpoint(
    recommendation_id: UUID | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    pending_for_me: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> dict:
    org_id = tenant_scope_service.require_data_access_organization_id(db_session, ctx)
    if not _can_list(db_session, ctx, org_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to list approval requests.")
    user_id = _resolve_user_id(db_session, ctx)
    admin_view = _admin_list_view(db_session, ctx, org_id)
    rows, total = approval_request_service.list_approval_requests(
        db_session,
        organization_id=org_id,
        user_id=user_id,
        recommendation_id=recommendation_id,
        status_filter=status_filter,
        pending_for_me=pending_for_me,
        limit=limit,
        offset=offset,
        admin_view=admin_view,
    )
    return {
        "items": [_to_read(db_session, r).model_dump(mode="json") for r in rows],
        "total": total,
    }


@router.get("/{request_id}", response_model=ApprovalRequestDetailRead)
def get_approval_request_endpoint(
    request_id: UUID,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> ApprovalRequestDetailRead:
    org_id = tenant_scope_service.require_data_access_organization_id(db_session, ctx)
    if not _can_list(db_session, ctx, org_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed.")
    req = approval_request_service.get_approval_request(
        db_session, request_id=request_id, organization_id=org_id
    )
    if req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval request not found.")
    user_id = _resolve_user_id(db_session, ctx)
    if not approval_request_service.user_may_view_request(
        db_session, req, user_id, _admin_list_view(db_session, ctx, org_id)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to view this request.")
    return _to_detail(db_session, req)


@router.post("/{request_id}/approve", response_model=ApprovalRequestDetailRead)
def approve_approval_request_endpoint(
    request_id: UUID,
    body: ApprovalDecisionBody | None = None,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> ApprovalRequestDetailRead:
    org_id = tenant_scope_service.require_data_access_organization_id(db_session, ctx)
    user_id = _resolve_user_id(db_session, ctx)
    comment = body.comment if body is not None else None
    try:
        req = approval_request_service.approve_assignment(
            db_session,
            request_id=request_id,
            organization_id=org_id,
            actor_user_id=user_id,
            actor_email=ctx.email,
            actor_role=ctx.role,
            comment=comment,
            actor_is_platform_root=ctx.is_platform_root,
        )
    except ValueError as exc:
        msg = str(exc)
        mapping = {
            "approval_request_not_found": (status.HTTP_404_NOT_FOUND, "Approval request not found."),
            "approval_request_not_actionable": (status.HTTP_409_CONFLICT, "This request is no longer open."),
            "forbidden": (status.HTTP_403_FORBIDDEN, "Approver role required."),
            "not_assigned_approver": (status.HTTP_403_FORBIDDEN, "You are not assigned to this request."),
            "assignment_already_acted": (status.HTTP_409_CONFLICT, "You have already acted on this assignment."),
        }
        if msg in mapping:
            code, detail = mapping[msg]
            raise HTTPException(status_code=code, detail=detail) from exc
        raise
    return _to_detail(db_session, req)


@router.post("/{request_id}/reject", response_model=ApprovalRequestDetailRead)
def reject_approval_request_endpoint(
    request_id: UUID,
    body: ApprovalDecisionBody | None = None,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> ApprovalRequestDetailRead:
    org_id = tenant_scope_service.require_data_access_organization_id(db_session, ctx)
    user_id = _resolve_user_id(db_session, ctx)
    comment = body.comment if body is not None else None
    try:
        req = approval_request_service.reject_assignment(
            db_session,
            request_id=request_id,
            organization_id=org_id,
            actor_user_id=user_id,
            actor_email=ctx.email,
            actor_role=ctx.role,
            comment=comment,
            actor_is_platform_root=ctx.is_platform_root,
        )
    except ValueError as exc:
        msg = str(exc)
        mapping = {
            "approval_request_not_found": (status.HTTP_404_NOT_FOUND, "Approval request not found."),
            "approval_request_not_actionable": (status.HTTP_409_CONFLICT, "This request is no longer open."),
            "forbidden": (status.HTTP_403_FORBIDDEN, "Approver role required."),
            "not_assigned_approver": (status.HTTP_403_FORBIDDEN, "You are not assigned to this request."),
            "assignment_already_acted": (status.HTTP_409_CONFLICT, "You have already acted on this assignment."),
        }
        if msg in mapping:
            code, detail = mapping[msg]
            raise HTTPException(status_code=code, detail=detail) from exc
        raise
    return _to_detail(db_session, req)
