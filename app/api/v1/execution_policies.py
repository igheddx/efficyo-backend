from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.user_context import UserContext, get_user_context
from app.models.execution_policy import ExecutionPolicy
from app.schemas.execution_policy_schema import ExecutionPolicyCreate, ExecutionPolicyPatch, ExecutionPolicyRead
from app.services import execution_audit_service, execution_policy_service, org_service, tenant_scope_service

router = APIRouter(prefix="/execution-policies", tags=["execution-policies"])


def _is_global_row(row: ExecutionPolicy) -> bool:
    return row.organization_id is None and row.tenant_id is None and row.cloud_account_id is None


def _require_policy_admin(db: Session, ctx: UserContext, organization_id: UUID) -> None:
    tenant_scope_service.assert_organization_accessible(db, ctx, organization_id)
    if ctx.is_platform_root:
        return
    if not org_service.can_manage_org(db, ctx, organization_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization admin or platform root required to manage execution policies.",
        )


def _require_root(ctx: UserContext) -> None:
    if not ctx.is_platform_root:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform root required for global execution policies.",
        )


@router.get("", response_model=list[ExecutionPolicyRead])
def list_execution_policies(
    organization_id: UUID = Query(..., description="Organization whose scoped policies are listed."),
    include_global: bool = Query(
        True,
        description="Include platform-wide (global) rows when caller is platform root.",
    ),
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> list[ExecutionPolicyRead]:
    org_ctx = tenant_scope_service.require_data_access_organization_id(db_session, ctx)
    if not ctx.is_platform_root and org_ctx != organization_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="organization_id must match your current organization.",
        )
    _require_policy_admin(db_session, ctx, organization_id)

    org_rows = execution_policy_service.list_policies_for_org(
        db_session, organization_id=organization_id, include_global=False
    )
    if ctx.is_platform_root and include_global:
        global_rows = (
            db_session.query(ExecutionPolicy)
            .filter(
                ExecutionPolicy.organization_id.is_(None),
                ExecutionPolicy.tenant_id.is_(None),
                ExecutionPolicy.cloud_account_id.is_(None),
            )
            .order_by(ExecutionPolicy.updated_at.desc())
            .all()
        )
        rows = list(global_rows) + org_rows
    else:
        rows = org_rows
    return [ExecutionPolicyRead.model_validate(r) for r in rows]


@router.post("", response_model=ExecutionPolicyRead, status_code=status.HTTP_201_CREATED)
def create_execution_policy(
    body: ExecutionPolicyCreate,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> ExecutionPolicyRead:
    is_global = body.organization_id is None and body.tenant_id is None and body.cloud_account_id is None
    if is_global:
        _require_root(ctx)
    else:
        if body.organization_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="organization_id is required for org-, tenant-, or account-scoped policies.",
            )
        org_ctx = tenant_scope_service.require_data_access_organization_id(db_session, ctx)
        if not ctx.is_platform_root and org_ctx != body.organization_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="organization_id must match your current organization.",
            )
        _require_policy_admin(db_session, ctx, body.organization_id)

    try:
        row = execution_policy_service.create_execution_policy(
            db_session,
            organization_id=body.organization_id,
            tenant_id=body.tenant_id,
            cloud_account_id=body.cloud_account_id,
            recommendation_type=body.recommendation_type,
            risk_class=body.risk_class,
            execution_mode=body.execution_mode,
            requires_all_approvals=body.requires_all_approvals,
            preflight_required=body.preflight_required,
            rollback_required=body.rollback_required,
            is_enabled=body.is_enabled,
            updated_by_email=ctx.email or None,
        )
    except ValueError as exc:
        msg = str(exc)
        mapping = {
            "tenant_and_org_required_for_cloud_scope": (
                status.HTTP_400_BAD_REQUEST,
                "tenant_id and organization_id are required when cloud_account_id is set.",
            ),
            "organization_required_for_tenant_scope": (
                status.HTTP_400_BAD_REQUEST,
                "organization_id is required when tenant_id is set.",
            ),
            "cloud_account_not_found": (status.HTTP_404_NOT_FOUND, "Cloud account not found."),
            "tenant_org_mismatch": (status.HTTP_400_BAD_REQUEST, "Tenant is not in the selected organization."),
            "recommendation_type_required": (status.HTTP_400_BAD_REQUEST, "recommendation_type is required."),
            "invalid_risk_class": (status.HTTP_400_BAD_REQUEST, "Invalid risk_class."),
            "invalid_execution_mode": (status.HTTP_400_BAD_REQUEST, "Invalid execution_mode."),
            "auto_mode_requires_safe_allowlisted_type": (
                status.HTTP_400_BAD_REQUEST,
                "approved_then_auto_allowed is only allowed for v1 safe execution types.",
            ),
        }
        if msg in mapping:
            code, detail = mapping[msg]
            raise HTTPException(status_code=code, detail=detail) from exc
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg) from exc

    execution_audit_service.log_execution_audit_event(
        db_session,
        event_type="policy_created",
        organization_id=row.organization_id,
        tenant_id=row.tenant_id,
        cloud_account_id=row.cloud_account_id,
        execution_policy_id=row.id,
        actor_user_id=ctx.user_id,
        actor_email=ctx.email or None,
        execution_trigger="policy_admin",
        allowed=True,
        detail_json=execution_policy_service.policy_to_dict(row),
    )
    return ExecutionPolicyRead.model_validate(row)


@router.patch("/{policy_id}", response_model=ExecutionPolicyRead)
def patch_execution_policy_endpoint(
    policy_id: UUID,
    body: ExecutionPolicyPatch,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> ExecutionPolicyRead:
    row = execution_policy_service.get_policy(db_session, policy_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution policy not found.")

    if _is_global_row(row):
        _require_root(ctx)
    else:
        oid = row.organization_id
        if oid is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Invalid policy row.")
        _require_policy_admin(db_session, ctx, oid)

    patch = body.model_dump(exclude_unset=True)
    try:
        execution_policy_service.patch_execution_policy(
            db_session,
            row,
            patch=patch,
            updated_by_email=ctx.email or None,
        )
    except ValueError as exc:
        msg = str(exc)
        if msg == "invalid_risk_class":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid risk_class.") from exc
        if msg == "invalid_execution_mode":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid execution_mode.") from exc
        if msg == "auto_mode_requires_safe_allowlisted_type":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="approved_then_auto_allowed is only allowed for v1 safe execution types.",
            ) from exc
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg) from exc

    db_session.refresh(row)
    execution_audit_service.log_execution_audit_event(
        db_session,
        event_type="policy_updated",
        organization_id=row.organization_id,
        tenant_id=row.tenant_id,
        cloud_account_id=row.cloud_account_id,
        execution_policy_id=row.id,
        actor_user_id=ctx.user_id,
        actor_email=ctx.email or None,
        execution_trigger="policy_admin",
        allowed=True,
        detail_json={**execution_policy_service.policy_to_dict(row), "patch": patch},
    )
    return ExecutionPolicyRead.model_validate(row)
