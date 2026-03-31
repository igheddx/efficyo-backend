from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.cost.service import cost_snapshot_service


class CostSyncJobRunner:
    def run(
        self,
        db: Session,
        *,
        tenant_id: UUID,
        cloud_account_id: UUID,
        sync_job_id: UUID | None = None,
        force_refresh: bool = False,
        actor_role: str | None = "system",
        actor_is_platform_root: bool = False,
        actor_is_system: bool = True,
    ) -> dict:
        snapshot = cost_snapshot_service.sync_cost_snapshot(
            db,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            feature_name="cost_sync_job",
            request_type="scheduled_sync",
            sync_job_id=sync_job_id,
            force_refresh=force_refresh,
            actor_role=actor_role,
            actor_is_platform_root=actor_is_platform_root,
            actor_is_system=actor_is_system,
        )
        return {"snapshot_id": str(snapshot.id), "snapshot_date": snapshot.snapshot_date.isoformat()}

