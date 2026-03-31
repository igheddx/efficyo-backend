from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.cost_window import account_cost_window_fields
from app.cost import repository
from app.cost.cache import CostSnapshotCache
from app.cost.metrics import log_cost_data_event


class CostQueryService:
    def __init__(self, *, cache: CostSnapshotCache | None = None) -> None:
        self._cache = cache or CostSnapshotCache()

    def _latest(self, db: Session, *, tenant_id: UUID, cloud_account_id: UUID):
        scope = repository.resolve_org_tenant_for_account(db, cloud_account_id)
        if scope is None:
            raise ValueError("cloud_account_scope_not_found")
        org_id, _ = scope
        return self._cache.latest(
            db,
            org_id=org_id,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
        )

    def get_summary(self, db: Session, *, tenant_id: UUID, cloud_account_id: UUID) -> dict:
        snapshot, freshness = self._latest(db, tenant_id=tenant_id, cloud_account_id=cloud_account_id)
        if snapshot is None:
            log_cost_data_event(
                event="snapshot_missing",
                tenant_id=tenant_id,
                cloud_account_id=cloud_account_id,
            )
            return {
                "start_date": "",
                "end_date": "",
                "total_cost": 0.0,
                "by_service": [],
                **account_cost_window_fields(),
                "is_snapshot_missing": True,
                "is_stale": True,
                "last_updated_at": None,
                "stale_after_minutes": freshness.stale_after_minutes,
                "data_source": "snapshot_missing",
            }
        source = "stale_snapshot_served" if freshness.is_stale else "snapshot_hit"
        log_cost_data_event(
            event=source,
            org_id=snapshot.org_id,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
        )
        return {
            "start_date": snapshot.period_start.isoformat(),
            "end_date": snapshot.period_end.isoformat(),
            "total_cost": float(snapshot.total_cost or 0),
            "by_service": list(snapshot.service_breakdown_json or []),
            **account_cost_window_fields(),
            "is_snapshot_missing": False,
            "is_stale": freshness.is_stale,
            "last_updated_at": snapshot.updated_at.isoformat() if snapshot.updated_at else None,
            "stale_after_minutes": freshness.stale_after_minutes,
            "data_source": source,
        }

    def get_ec2_other_breakdown(self, db: Session, *, tenant_id: UUID, cloud_account_id: UUID) -> dict:
        snapshot, freshness = self._latest(db, tenant_id=tenant_id, cloud_account_id=cloud_account_id)
        if snapshot is None:
            return {
                "ec2_other_total": 0.0,
                "breakdown": [],
                **account_cost_window_fields(),
                "is_snapshot_missing": True,
                "is_stale": True,
                "last_updated_at": None,
                "stale_after_minutes": freshness.stale_after_minutes,
                "data_source": "snapshot_missing",
            }
        source = "stale_snapshot_served" if freshness.is_stale else "snapshot_hit"
        breakdown = list(snapshot.ec2_other_breakdown_json or [])
        total = sum(Decimal(str(i.get("amount", 0))) for i in breakdown)
        return {
            "ec2_other_total": float(round(total, 2)),
            "breakdown": breakdown,
            **account_cost_window_fields(),
            "is_snapshot_missing": False,
            "is_stale": freshness.is_stale,
            "last_updated_at": snapshot.updated_at.isoformat() if snapshot.updated_at else None,
            "stale_after_minutes": freshness.stale_after_minutes,
            "data_source": source,
        }

    def get_trends(self, db: Session, *, tenant_id: UUID, cloud_account_id: UUID) -> list[dict]:
        snapshot, _freshness = self._latest(db, tenant_id=tenant_id, cloud_account_id=cloud_account_id)
        return list(snapshot.cost_trends_json or []) if snapshot else []

    def get_cost_trends_over_time(self, db: Session, *, tenant_id: UUID, cloud_account_id: UUID, days: int) -> dict:
        snapshot, freshness = self._latest(db, tenant_id=tenant_id, cloud_account_id=cloud_account_id)
        if snapshot is None:
            return {
                "days": days,
                "points": [],
                **account_cost_window_fields(),
                "is_snapshot_missing": True,
                "is_stale": True,
                "data_source": "snapshot_missing",
            }
        points = list(snapshot.daily_costs_json or [])
        points = points[-days:] if days > 0 else points
        source = "stale_snapshot_served" if freshness.is_stale else "snapshot_hit"
        return {
            "days": days,
            "points": points,
            **account_cost_window_fields(),
            "is_snapshot_missing": False,
            "is_stale": freshness.is_stale,
            "last_updated_at": snapshot.updated_at.isoformat() if snapshot.updated_at else None,
            "stale_after_minutes": freshness.stale_after_minutes,
            "data_source": source,
        }

    def get_freshness(self, db: Session, *, tenant_id: UUID, cloud_account_id: UUID) -> dict:
        snapshot, freshness = self._latest(db, tenant_id=tenant_id, cloud_account_id=cloud_account_id)
        return {
            "is_snapshot_missing": snapshot is None,
            "is_stale": freshness.is_stale,
            "last_updated_at": snapshot.updated_at.isoformat() if snapshot and snapshot.updated_at else None,
            "stale_after_minutes": freshness.stale_after_minutes,
        }


cost_query_service = CostQueryService()

