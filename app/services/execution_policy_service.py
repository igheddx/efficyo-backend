"""Execution policy CRUD and resolution (cloud > tenant > org > global > builtin)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.db import utc_now
from app.models.cloud_account import CloudAccount
from app.models.execution_policy import ExecutionPolicy
from app.models.tenant import Tenant
from app.services.execution_constants import is_safe_auto_execution_type

EXECUTION_MODES = frozenset({"manual_only", "approved_then_manual", "approved_then_auto_allowed"})
RISK_CLASSES = frozenset({"any", "high", "medium", "low"})


@dataclass(frozen=True)
class ResolvedExecutionPolicy:
    """Effective policy for a recommendation in context."""

    policy_row_id: UUID | None
    scope_level: str
    recommendation_type: str
    risk_class: str
    execution_mode: str
    requires_all_approvals: bool
    preflight_required: bool
    rollback_required: bool


BUILTIN_POLICY = ResolvedExecutionPolicy(
    policy_row_id=None,
    scope_level="builtin",
    recommendation_type="*",
    risk_class="any",
    execution_mode="approved_then_manual",
    requires_all_approvals=True,
    preflight_required=False,
    rollback_required=True,
)


def _risk_matches(row_risk: str, recommendation_risk: str) -> bool:
    rr = (row_risk or "any").strip().lower()
    if rr == "any":
        return True
    return rr == (recommendation_risk or "").strip().lower()


def _type_matches(row_type: str, recommendation_type: str) -> bool:
    rt = (row_type or "").strip().lower()
    if rt == "*":
        return True
    return rt == (recommendation_type or "").strip().lower()


def _scope_applies(
    row: ExecutionPolicy,
    *,
    organization_id: UUID,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> bool:
    if row.cloud_account_id is not None:
        return (
            row.cloud_account_id == cloud_account_id
            and row.tenant_id == tenant_id
            and row.organization_id == organization_id
        )
    if row.tenant_id is not None:
        return row.tenant_id == tenant_id and row.organization_id == organization_id
    if row.organization_id is not None:
        return row.organization_id == organization_id
    return row.organization_id is None and row.tenant_id is None and row.cloud_account_id is None


def _scope_rank(row: ExecutionPolicy) -> int:
    if row.cloud_account_id is not None:
        return 4
    if row.tenant_id is not None:
        return 3
    if row.organization_id is not None:
        return 2
    return 1


def _type_rank(row: ExecutionPolicy) -> int:
    return 2 if (row.recommendation_type or "").strip().lower() != "*" else 1


def resolve_execution_policy(
    db: Session,
    *,
    organization_id: UUID,
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_type: str,
    recommendation_risk_level: str,
) -> ResolvedExecutionPolicy:
    rows = (
        db.query(ExecutionPolicy)
        .filter(ExecutionPolicy.is_enabled.is_(True))
        .all()
    )
    candidates: list[ExecutionPolicy] = []
    for row in rows:
        if not _scope_applies(row, organization_id=organization_id, tenant_id=tenant_id, cloud_account_id=cloud_account_id):
            continue
        if not _type_matches(row.recommendation_type, recommendation_type):
            continue
        if not _risk_matches(row.risk_class, recommendation_risk_level):
            continue
        candidates.append(row)

    if not candidates:
        return BUILTIN_POLICY

    _epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    candidates.sort(
        key=lambda r: (_scope_rank(r), _type_rank(r), r.updated_at or _epoch),
        reverse=True,
    )
    best = candidates[0]
    return ResolvedExecutionPolicy(
        policy_row_id=best.id,
        scope_level=(
            "cloud_account"
            if best.cloud_account_id
            else "tenant"
            if best.tenant_id
            else "organization"
            if best.organization_id
            else "global"
        ),
        recommendation_type=best.recommendation_type,
        risk_class=best.risk_class,
        execution_mode=(best.execution_mode or "approved_then_manual").strip().lower(),
        requires_all_approvals=bool(best.requires_all_approvals),
        preflight_required=bool(best.preflight_required),
        rollback_required=bool(best.rollback_required),
    )


def list_policies_for_org(
    db: Session,
    *,
    organization_id: UUID,
    include_global: bool = True,
) -> list[ExecutionPolicy]:
    q = db.query(ExecutionPolicy).filter(ExecutionPolicy.organization_id == organization_id)
    org_rows = q.order_by(ExecutionPolicy.updated_at.desc()).all()
    if not include_global:
        return org_rows
    global_rows = (
        db.query(ExecutionPolicy)
        .filter(
            ExecutionPolicy.organization_id.is_(None),
            ExecutionPolicy.tenant_id.is_(None),
            ExecutionPolicy.cloud_account_id.is_(None),
        )
        .order_by(ExecutionPolicy.updated_at.desc())
        .all()
    )
    return global_rows + org_rows


def get_policy(db: Session, policy_id: UUID) -> ExecutionPolicy | None:
    return db.query(ExecutionPolicy).filter(ExecutionPolicy.id == policy_id).first()


def validate_policy_scope(
    db: Session,
    *,
    organization_id: UUID | None,
    tenant_id: UUID | None,
    cloud_account_id: UUID | None,
) -> None:
    if cloud_account_id is not None:
        if tenant_id is None or organization_id is None:
            raise ValueError("tenant_and_org_required_for_cloud_scope")
        ca = (
            db.query(CloudAccount)
            .filter(CloudAccount.id == cloud_account_id, CloudAccount.tenant_id == tenant_id)
            .first()
        )
        if ca is None:
            raise ValueError("cloud_account_not_found")
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if tenant is None or tenant.organization_id != organization_id:
            raise ValueError("tenant_org_mismatch")
        return
    if tenant_id is not None:
        if organization_id is None:
            raise ValueError("organization_required_for_tenant_scope")
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if tenant is None or tenant.organization_id != organization_id:
            raise ValueError("tenant_org_mismatch")
        return
    if organization_id is not None:
        return
    # global
    return


def create_execution_policy(
    db: Session,
    *,
    organization_id: UUID | None,
    tenant_id: UUID | None,
    cloud_account_id: UUID | None,
    recommendation_type: str,
    risk_class: str,
    execution_mode: str,
    requires_all_approvals: bool,
    preflight_required: bool,
    rollback_required: bool,
    is_enabled: bool,
    updated_by_email: str | None,
) -> ExecutionPolicy:
    validate_policy_scope(db, organization_id=organization_id, tenant_id=tenant_id, cloud_account_id=cloud_account_id)
    rt = (recommendation_type or "").strip().lower()
    if not rt:
        raise ValueError("recommendation_type_required")
    rc = (risk_class or "any").strip().lower()
    if rc not in RISK_CLASSES:
        raise ValueError("invalid_risk_class")
    em = (execution_mode or "").strip().lower()
    if em not in EXECUTION_MODES:
        raise ValueError("invalid_execution_mode")
    if em == "approved_then_auto_allowed":
        if rt == "*" or not is_safe_auto_execution_type(rt):
            raise ValueError("auto_mode_requires_safe_allowlisted_type")

    row = ExecutionPolicy(
        organization_id=organization_id,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        recommendation_type=rt,
        risk_class=rc,
        execution_mode=em,
        requires_all_approvals=requires_all_approvals,
        preflight_required=preflight_required,
        rollback_required=rollback_required,
        is_enabled=is_enabled,
        updated_by_email=updated_by_email,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def patch_execution_policy(
    db: Session,
    row: ExecutionPolicy,
    *,
    patch: dict[str, Any],
    updated_by_email: str | None,
) -> ExecutionPolicy:
    if "risk_class" in patch and patch["risk_class"] is not None:
        rc = str(patch["risk_class"]).strip().lower()
        if rc not in RISK_CLASSES:
            raise ValueError("invalid_risk_class")
        row.risk_class = rc
    if "execution_mode" in patch and patch["execution_mode"] is not None:
        em = str(patch["execution_mode"]).strip().lower()
        if em not in EXECUTION_MODES:
            raise ValueError("invalid_execution_mode")
        row.execution_mode = em
    if "requires_all_approvals" in patch and patch["requires_all_approvals"] is not None:
        row.requires_all_approvals = bool(patch["requires_all_approvals"])
    if "preflight_required" in patch and patch["preflight_required"] is not None:
        row.preflight_required = bool(patch["preflight_required"])
    if "rollback_required" in patch and patch["rollback_required"] is not None:
        row.rollback_required = bool(patch["rollback_required"])
    if "is_enabled" in patch and patch["is_enabled"] is not None:
        row.is_enabled = bool(patch["is_enabled"])
    row.updated_by_email = updated_by_email
    row.updated_at = utc_now()

    em = row.execution_mode.strip().lower()
    rt = row.recommendation_type.strip().lower()
    if em == "approved_then_auto_allowed" and (rt == "*" or not is_safe_auto_execution_type(rt)):
        raise ValueError("auto_mode_requires_safe_allowlisted_type")

    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def policy_to_dict(row: ExecutionPolicy) -> dict[str, Any]:
    return {
        "id": row.id,
        "organization_id": row.organization_id,
        "tenant_id": row.tenant_id,
        "cloud_account_id": row.cloud_account_id,
        "recommendation_type": row.recommendation_type,
        "risk_class": row.risk_class,
        "execution_mode": row.execution_mode,
        "requires_all_approvals": row.requires_all_approvals,
        "preflight_required": row.preflight_required,
        "rollback_required": row.rollback_required,
        "is_enabled": row.is_enabled,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "updated_by_email": row.updated_by_email,
    }
