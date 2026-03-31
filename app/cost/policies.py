from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.models.cost_snapshot import CostSnapshot, CostSyncPolicy


@dataclass(frozen=True)
class FreshnessState:
    is_stale: bool
    status: str
    stale_after_minutes: int
    last_updated: datetime | None


class CostFreshnessPolicy:
    def evaluate(self, snapshot: CostSnapshot | None, policy: CostSyncPolicy) -> FreshnessState:
        stale_after = int(getattr(policy, "stale_after_minutes", 1440) or 1440)
        if snapshot is None:
            return FreshnessState(
                is_stale=True,
                status="missing",
                stale_after_minutes=stale_after,
                last_updated=None,
            )
        updated = snapshot.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        is_stale = datetime.now(timezone.utc) >= (updated + timedelta(minutes=stale_after))
        return FreshnessState(
            is_stale=is_stale,
            status="stale" if is_stale else "fresh",
            stale_after_minutes=stale_after,
            last_updated=updated,
        )

