from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.cost import repository
from app.models.cost_snapshot import CostSyncPolicy


class CostQuotaExceededError(RuntimeError):
    pass


class CostGuardService:
    def assert_live_call_allowed(
        self,
        db: Session,
        *,
        policy: CostSyncPolicy,
        org_id: UUID,
        tenant_id: UUID,
        cloud_account_id: UUID,
        sync_job_id: UUID | None,
    ) -> None:
        if not bool(policy.enabled):
            raise CostQuotaExceededError("cost_sync_disabled")
        org_day = repository.count_usage_since_start_of_day(
            db,
            org_id=org_id,
            provider=policy.provider,
        )
        if org_day >= int(policy.max_calls_per_org_day):
            raise CostQuotaExceededError("org_daily_quota_exceeded")
        account_day = repository.count_usage_since_start_of_day(
            db,
            org_id=org_id,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            provider=policy.provider,
        )
        if account_day >= int(policy.max_calls_per_day):
            raise CostQuotaExceededError("account_daily_quota_exceeded")
        if sync_job_id is not None:
            by_job = repository.count_usage_for_job(db, sync_job_id=sync_job_id)
            if by_job >= int(policy.max_calls_per_job):
                raise CostQuotaExceededError("job_quota_exceeded")

    def assert_force_refresh_allowed(
        self,
        *,
        policy: CostSyncPolicy,
        actor_role: str | None,
        actor_is_platform_root: bool,
        actor_is_system: bool,
    ) -> None:
        if not actor_role and not actor_is_platform_root and not actor_is_system:
            raise CostQuotaExceededError("force_refresh_forbidden")
        if actor_is_system or actor_is_platform_root:
            return
        role = (actor_role or "").strip().lower()
        if role == "org_admin":
            return
        if role == "admin" and bool(policy.allow_admin_force_refresh):
            return
        raise CostQuotaExceededError("force_refresh_forbidden")

