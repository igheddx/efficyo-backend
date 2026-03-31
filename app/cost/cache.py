from __future__ import annotations

from sqlalchemy.orm import Session

from app.cost import repository
from app.cost.policies import CostFreshnessPolicy, FreshnessState
from app.models.cost_snapshot import CostSnapshot


class CostSnapshotCache:
    def __init__(self, *, freshness_policy: CostFreshnessPolicy | None = None) -> None:
        self._freshness = freshness_policy or CostFreshnessPolicy()

    def latest(
        self,
        db: Session,
        *,
        org_id,
        tenant_id,
        cloud_account_id,
        provider: str = "aws",
    ) -> tuple[CostSnapshot | None, FreshnessState]:
        policy = repository.get_effective_policy(
            db,
            org_id=org_id,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            provider=provider,
        )
        snapshot = repository.get_latest_snapshot(
            db,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            provider=provider,
        )
        freshness = self._freshness.evaluate(snapshot, policy)
        return snapshot, freshness

