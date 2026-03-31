from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.cost import repository
from app.cost.cache import CostSnapshotCache
from app.cost.client import CostExplorerClient, CostFetchInProgressError
from app.cost.guards import CostQuotaExceededError
from app.cost.metrics import log_cost_data_event
from app.cost.models import CostRequestContext
from app.cost.normalizers import summarize_daily_rows_to_points
from app.services import cloud_account_service


class CostSnapshotService:
    def __init__(self, *, client: CostExplorerClient | None = None, cache: CostSnapshotCache | None = None) -> None:
        self._client = client or CostExplorerClient()
        self._cache = cache or CostSnapshotCache()

    def sync_cost_snapshot(
        self,
        db: Session,
        *,
        tenant_id: UUID,
        cloud_account_id: UUID,
        feature_name: str,
        request_type: str,
        sync_job_id: UUID | None = None,
        force_refresh: bool = False,
        actor_role: str | None = None,
        actor_is_platform_root: bool = False,
        actor_is_system: bool = False,
    ):
        cloud_account = cloud_account_service.get_cloud_account_or_raise(db, tenant_id, cloud_account_id)
        scope = repository.resolve_org_tenant_for_account(db, cloud_account_id)
        if scope is None:
            raise ValueError("cloud_account_scope_not_found")
        org_id, _ = scope
        policy = repository.get_effective_policy(
            db,
            org_id=org_id,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            provider="aws",
        )
        latest, freshness = self._cache.latest(
            db,
            org_id=org_id,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            provider="aws",
        )
        if force_refresh:
            self._client.assert_force_refresh_allowed(
                policy=policy,
                actor_role=actor_role,
                actor_is_platform_root=actor_is_platform_root,
                actor_is_system=actor_is_system,
            )
        if latest is not None and not freshness.is_stale and not force_refresh:
            return latest

        ctx = CostRequestContext(
            org_id=org_id,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            provider="aws",
            feature_name=feature_name,
            request_type=request_type,
            sync_job_id=sync_job_id,
        )
        try:
            summary = self._client.fetch_summary(db, cloud_account=cloud_account, ctx=ctx, policy=policy)
            daily = self._client.fetch_daily_by_service(
                db,
                cloud_account=cloud_account,
                days=30,
                ctx=ctx,
                policy=policy,
            )
            ec2_other = self._client.fetch_ec2_other_breakdown(db, cloud_account=cloud_account, ctx=ctx, policy=policy)
        except CostQuotaExceededError as exc:
            if str(exc) == "force_refresh_forbidden":
                raise
            # Fail-safe: keep serving last known snapshot if live fetch is blocked.
            log_cost_data_event(
                event="blocked_by_quota",
                org_id=org_id,
                tenant_id=tenant_id,
                cloud_account_id=cloud_account_id,
                details={"reason": str(exc)},
            )
            if latest is not None:
                log_cost_data_event(
                    event="stale_snapshot_served",
                    org_id=org_id,
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    details={"reason": str(exc)},
                )
                return latest
            raise
        except CostFetchInProgressError as exc:
            log_cost_data_event(
                event="blocked_by_inflight_lock",
                org_id=org_id,
                tenant_id=tenant_id,
                cloud_account_id=cloud_account_id,
                details={"reason": str(exc)},
            )
            if latest is not None:
                log_cost_data_event(
                    event="stale_snapshot_served",
                    org_id=org_id,
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    details={"reason": str(exc)},
                )
                return latest
            raise
        except Exception:
            # Fail-safe: do not aggressively retry in loops; fall back to latest snapshot.
            if latest is not None:
                log_cost_data_event(
                    event="stale_snapshot_served",
                    org_id=org_id,
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    details={"reason": "live_fetch_exception"},
                )
                return latest
            raise

        points = summarize_daily_rows_to_points(daily)
        trends = self._build_trends(daily)
        row = repository.upsert_snapshot(
            db,
            org_id=org_id,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            provider="aws",
            snapshot_date=date.today(),
            period_start=date.fromisoformat(summary.get("start_date")),
            period_end=date.fromisoformat(summary.get("end_date")),
            granularity="DAILY",
            total_cost=Decimal(str(summary.get("total_cost", 0))),
            currency=str(summary.get("currency", "USD")),
            service_breakdown_json=list(summary.get("by_service", []) or []),
            daily_costs_json=points,
            cost_trends_json=trends,
            ec2_other_breakdown_json=list(ec2_other.get("breakdown", []) or []),
            waf_monthly_cost=Decimal(str(self._waf_total(summary.get("by_service", [])))),
            source_job_id=sync_job_id,
            freshness_status="fresh",
            stale_after_minutes=int(policy.stale_after_minutes),
        )
        db.flush()
        return row

    @staticmethod
    def _waf_total(by_service: list[dict]) -> float:
        total = Decimal("0")
        for item in by_service or []:
            name = (item.get("service") or "").strip()
            if name == "AWS WAF" or name.startswith("AWS WAF "):
                total += Decimal(str(item.get("amount", 0)))
        return float(total)

    @staticmethod
    def _build_trends(daily_rows: list[dict]) -> list[dict]:
        if not daily_rows:
            return []
        prev = daily_rows[-14:-7] if len(daily_rows) >= 14 else daily_rows[:-7]
        curr = daily_rows[-7:]
        prev_by_service: dict[str, Decimal] = {}
        curr_by_service: dict[str, Decimal] = {}
        for row in prev:
            for k, v in (row.get("by_service") or {}).items():
                prev_by_service[k] = prev_by_service.get(k, Decimal("0")) + Decimal(str(v))
        for row in curr:
            for k, v in (row.get("by_service") or {}).items():
                curr_by_service[k] = curr_by_service.get(k, Decimal("0")) + Decimal(str(v))
        services = sorted(set(prev_by_service.keys()) | set(curr_by_service.keys()))
        out: list[dict] = []
        for service in services:
            p = prev_by_service.get(service, Decimal("0"))
            c = curr_by_service.get(service, Decimal("0"))
            pct = 100.0 if (p == 0 and c > 0) else (float(((c - p) / p) * Decimal("100")) if p > 0 else 0.0)
            trend = "stable"
            if pct > 15:
                trend = "increasing"
            elif pct < -15:
                trend = "decreasing"
            out.append(
                {
                    "service": service,
                    "trend": trend,
                    "percent_change": round(pct, 2),
                    "current_cost": float(round(c, 2)),
                    "previous_cost": float(round(p, 2)),
                }
            )
        return out


cost_snapshot_service = CostSnapshotService()

