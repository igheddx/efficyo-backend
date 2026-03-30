"""Append-only audit trail for execution policies and execution attempts."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.execution_audit_event import ExecutionAuditEvent

logger = logging.getLogger(__name__)


def log_execution_audit_event(
    db: Session,
    *,
    event_type: str,
    organization_id: UUID | None = None,
    tenant_id: UUID | None = None,
    cloud_account_id: UUID | None = None,
    recommendation_id: UUID | None = None,
    execution_policy_id: UUID | None = None,
    actor_user_id: UUID | None = None,
    actor_email: str | None = None,
    execution_trigger: str | None = None,
    allowed: bool | None = None,
    blocking_reason: str | None = None,
    detail_json: dict[str, Any] | None = None,
) -> None:
    row = ExecutionAuditEvent(
        event_type=event_type,
        organization_id=organization_id,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        recommendation_id=recommendation_id,
        execution_policy_id=execution_policy_id,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        execution_trigger=execution_trigger,
        allowed=allowed,
        blocking_reason=blocking_reason,
        detail_json=detail_json,
    )
    db.add(row)
    try:
        db.commit()
    except Exception:
        logger.exception("execution_audit_event commit failed")
        db.rollback()
