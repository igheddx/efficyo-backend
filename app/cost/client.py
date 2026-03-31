from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.cost.guards import CostGuardService
from app.cost.metrics import log_cost_api_call
from app.cost.models import CostRequestContext
from app.cost import repository
from app.models.cloud_account import CloudAccount
from app.services import cost_explorer_service


class CostFetchInProgressError(RuntimeError):
    pass


class CostExplorerClient:
    """
    Single funnel for live paid cost API calls.
    Do not call Cost Explorer directly outside this client.
    """

    def __init__(self, *, guards: CostGuardService | None = None) -> None:
        self._guards = guards or CostGuardService()

    @staticmethod
    def _signature(api_name: str, payload: dict[str, Any]) -> str:
        raw = f"{api_name}:{json.dumps(payload, sort_keys=True, default=str)}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _guard_and_call(self, db: Session, *, ctx: CostRequestContext, policy, api_name: str, payload: dict, fn):
        self._guards.assert_live_call_allowed(
            db,
            policy=policy,
            org_id=ctx.org_id,
            tenant_id=ctx.tenant_id,
            cloud_account_id=ctx.cloud_account_id,
            sync_job_id=ctx.sync_job_id,
        )
        sig = self._signature(api_name, payload)
        acquired = repository.acquire_fetch_lock(
            db,
            org_id=ctx.org_id,
            tenant_id=ctx.tenant_id,
            cloud_account_id=ctx.cloud_account_id,
            provider=ctx.provider,
            request_signature=sig,
            lock_reason=api_name,
        )
        if not acquired:
            raise CostFetchInProgressError("duplicate_cost_fetch_in_progress")
        try:
            result = fn()
            log_cost_api_call(
                db,
                ctx=ctx,
                request_signature=sig,
                was_cache_hit=False,
                api_name=api_name,
                estimated_call_cost=Decimal("0.01"),
            )
            return result
        finally:
            repository.release_fetch_lock(db, request_signature=sig)

    def assert_force_refresh_allowed(
        self,
        *,
        policy,
        actor_role: str | None,
        actor_is_platform_root: bool,
        actor_is_system: bool,
    ) -> None:
        self._guards.assert_force_refresh_allowed(
            policy=policy,
            actor_role=actor_role,
            actor_is_platform_root=actor_is_platform_root,
            actor_is_system=actor_is_system,
        )

    def fetch_summary(self, db: Session, *, cloud_account: CloudAccount, ctx: CostRequestContext, policy) -> dict:
        return self._guard_and_call(
            db,
            ctx=ctx,
            policy=policy,
            api_name="ce.get_cost_and_usage.summary",
            payload={"role_arn": cloud_account.role_arn},
            fn=lambda: cost_explorer_service.fetch_cost_summary(role_arn=cloud_account.role_arn),
        )

    def fetch_daily_by_service(
        self,
        db: Session,
        *,
        cloud_account: CloudAccount,
        days: int,
        ctx: CostRequestContext,
        policy,
    ) -> list[dict]:
        return self._guard_and_call(
            db,
            ctx=ctx,
            policy=policy,
            api_name="ce.get_cost_and_usage.daily_by_service",
            payload={"role_arn": cloud_account.role_arn, "days": days},
            fn=lambda: cost_explorer_service.fetch_daily_unblended_cost_by_service(
                role_arn=cloud_account.role_arn,
                days=days,
            ),
        )

    def fetch_ec2_other_breakdown(self, db: Session, *, cloud_account: CloudAccount, ctx: CostRequestContext, policy) -> dict:
        return self._guard_and_call(
            db,
            ctx=ctx,
            policy=policy,
            api_name="ce.get_cost_and_usage.ec2_other",
            payload={"role_arn": cloud_account.role_arn},
            fn=lambda: cost_explorer_service.fetch_ec2_other_breakdown(role_arn=cloud_account.role_arn),
        )

