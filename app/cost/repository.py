from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.models.cloud_account import CloudAccount
from app.models.cost_snapshot import CostApiUsageLog, CostFetchLock, CostSnapshot, CostSyncPolicy
from app.models.tenant import Tenant


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def resolve_org_tenant_for_account(db: Session, cloud_account_id: UUID) -> tuple[UUID, UUID] | None:
    row = (
        db.query(CloudAccount.tenant_id, Tenant.organization_id)
        .join(Tenant, Tenant.id == CloudAccount.tenant_id)
        .filter(CloudAccount.id == cloud_account_id)
        .first()
    )
    if row is None or row[1] is None:
        return None
    return row[1], row[0]


def get_effective_policy(
    db: Session,
    *,
    org_id: UUID,
    tenant_id: UUID,
    cloud_account_id: UUID,
    provider: str,
) -> CostSyncPolicy:
    account = (
        db.query(CostSyncPolicy)
        .filter(
            CostSyncPolicy.provider == provider,
            CostSyncPolicy.org_id == org_id,
            CostSyncPolicy.tenant_id == tenant_id,
            CostSyncPolicy.cloud_account_id == cloud_account_id,
        )
        .first()
    )
    if account is not None:
        return account
    tenant = (
        db.query(CostSyncPolicy)
        .filter(
            CostSyncPolicy.provider == provider,
            CostSyncPolicy.org_id == org_id,
            CostSyncPolicy.tenant_id == tenant_id,
            CostSyncPolicy.cloud_account_id.is_(None),
        )
        .first()
    )
    if tenant is not None:
        return tenant
    org = (
        db.query(CostSyncPolicy)
        .filter(
            CostSyncPolicy.provider == provider,
            CostSyncPolicy.org_id == org_id,
            CostSyncPolicy.tenant_id.is_(None),
            CostSyncPolicy.cloud_account_id.is_(None),
        )
        .first()
    )
    if org is not None:
        return org
    default = CostSyncPolicy(org_id=org_id, provider=provider)
    db.add(default)
    db.flush()
    return default


def get_latest_snapshot(
    db: Session,
    *,
    tenant_id: UUID,
    cloud_account_id: UUID,
    provider: str = "aws",
) -> CostSnapshot | None:
    return (
        db.query(CostSnapshot)
        .filter(
            CostSnapshot.tenant_id == tenant_id,
            CostSnapshot.cloud_account_id == cloud_account_id,
            CostSnapshot.provider == provider,
        )
        .order_by(CostSnapshot.snapshot_date.desc(), CostSnapshot.created_at.desc())
        .first()
    )


def upsert_snapshot(
    db: Session,
    *,
    org_id: UUID,
    tenant_id: UUID,
    cloud_account_id: UUID,
    provider: str,
    snapshot_date: date,
    period_start: date,
    period_end: date,
    granularity: str,
    total_cost: Decimal,
    currency: str,
    service_breakdown_json: list[dict],
    daily_costs_json: list[dict],
    cost_trends_json: list[dict],
    ec2_other_breakdown_json: list[dict],
    waf_monthly_cost: Decimal,
    source_job_id: UUID | None,
    freshness_status: str,
    stale_after_minutes: int,
) -> CostSnapshot:
    row = (
        db.query(CostSnapshot)
        .filter(
            CostSnapshot.tenant_id == tenant_id,
            CostSnapshot.cloud_account_id == cloud_account_id,
            CostSnapshot.provider == provider,
            CostSnapshot.snapshot_date == snapshot_date,
        )
        .first()
    )
    if row is None:
        row = CostSnapshot(
            org_id=org_id,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            provider=provider,
            snapshot_date=snapshot_date,
        )
        db.add(row)
    row.period_start = period_start
    row.period_end = period_end
    row.granularity = granularity
    row.total_cost = total_cost
    row.currency = currency
    row.service_breakdown_json = service_breakdown_json
    row.daily_costs_json = daily_costs_json
    row.cost_trends_json = cost_trends_json
    row.ec2_other_breakdown_json = ec2_other_breakdown_json
    row.waf_monthly_cost = waf_monthly_cost
    row.source_job_id = source_job_id
    row.freshness_status = freshness_status
    row.stale_after_minutes = stale_after_minutes
    row.updated_at = utcnow()
    db.flush()
    return row


def log_api_usage(
    db: Session,
    *,
    org_id: UUID,
    tenant_id: UUID,
    cloud_account_id: UUID,
    provider: str,
    sync_job_id: UUID | None,
    feature_name: str,
    request_type: str,
    request_signature: str,
    was_cache_hit: bool,
    api_name: str,
    estimated_call_cost: Decimal,
) -> CostApiUsageLog:
    row = CostApiUsageLog(
        org_id=org_id,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        provider=provider,
        sync_job_id=sync_job_id,
        feature_name=feature_name,
        request_type=request_type,
        request_signature=request_signature,
        was_cache_hit=was_cache_hit,
        api_name=api_name,
        estimated_call_cost=estimated_call_cost,
    )
    db.add(row)
    db.flush()
    return row


def count_usage_since_start_of_day(
    db: Session,
    *,
    org_id: UUID | None = None,
    tenant_id: UUID | None = None,
    cloud_account_id: UUID | None = None,
    provider: str = "aws",
    now: datetime | None = None,
) -> int:
    now = now or utcnow()
    sod = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    q = db.query(func.count(CostApiUsageLog.id)).filter(
        CostApiUsageLog.provider == provider,
        CostApiUsageLog.was_cache_hit.is_(False),
        CostApiUsageLog.created_at >= sod,
    )
    if org_id is not None:
        q = q.filter(CostApiUsageLog.org_id == org_id)
    if tenant_id is not None:
        q = q.filter(CostApiUsageLog.tenant_id == tenant_id)
    if cloud_account_id is not None:
        q = q.filter(CostApiUsageLog.cloud_account_id == cloud_account_id)
    return int(q.scalar() or 0)


def count_usage_for_job(db: Session, *, sync_job_id: UUID) -> int:
    return int(
        db.query(func.count(CostApiUsageLog.id))
        .filter(CostApiUsageLog.sync_job_id == sync_job_id, CostApiUsageLog.was_cache_hit.is_(False))
        .scalar()
        or 0
    )


def list_usage_logs(
    db: Session,
    *,
    tenant_id: UUID,
    cloud_account_id: UUID,
    provider: str = "aws",
    limit: int = 100,
) -> tuple[list[CostApiUsageLog], int]:
    q = db.query(CostApiUsageLog).filter(
        CostApiUsageLog.tenant_id == tenant_id,
        CostApiUsageLog.cloud_account_id == cloud_account_id,
        CostApiUsageLog.provider == provider,
    )
    total = int(q.count())
    rows = q.order_by(CostApiUsageLog.created_at.desc()).limit(limit).all()
    return rows, total


def summarize_usage(
    db: Session,
    *,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    org_id: UUID | None = None,
    tenant_id: UUID | None = None,
    cloud_account_id: UUID | None = None,
    provider: str = "aws",
) -> dict:
    q = db.query(CostApiUsageLog).filter(CostApiUsageLog.provider == provider)
    if start_at is not None:
        q = q.filter(CostApiUsageLog.created_at >= start_at)
    if end_at is not None:
        q = q.filter(CostApiUsageLog.created_at < end_at)
    if org_id is not None:
        q = q.filter(CostApiUsageLog.org_id == org_id)
    if tenant_id is not None:
        q = q.filter(CostApiUsageLog.tenant_id == tenant_id)
    if cloud_account_id is not None:
        q = q.filter(CostApiUsageLog.cloud_account_id == cloud_account_id)
    total_calls = int(q.count())
    live_calls = int(q.filter(CostApiUsageLog.was_cache_hit.is_(False)).count())
    cache_hits = int(q.filter(CostApiUsageLog.was_cache_hit.is_(True)).count())
    estimated = q.with_entities(func.coalesce(func.sum(CostApiUsageLog.estimated_call_cost), 0)).scalar() or 0
    return {
        "total_calls": total_calls,
        "live_calls": live_calls,
        "cache_hits": cache_hits,
        "estimated_call_cost_total": float(estimated),
    }


def aggregate_usage(
    db: Session,
    *,
    group_by: str,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    org_id: UUID | None = None,
    tenant_id: UUID | None = None,
    cloud_account_id: UUID | None = None,
    provider: str = "aws",
    limit: int = 200,
) -> list[dict]:
    group_map = {
        "org": CostApiUsageLog.org_id,
        "account": CostApiUsageLog.cloud_account_id,
        "job": CostApiUsageLog.sync_job_id,
        "feature": CostApiUsageLog.feature_name,
    }
    group_col = group_map.get(group_by)
    if group_col is None:
        raise ValueError("unsupported_group_by")
    q = db.query(
        group_col.label("group_key"),
        func.count(CostApiUsageLog.id).label("total_calls"),
        func.sum(case((CostApiUsageLog.was_cache_hit.is_(False), 1), else_=0)).label("live_calls"),
        func.sum(case((CostApiUsageLog.was_cache_hit.is_(True), 1), else_=0)).label("cache_hits"),
        func.coalesce(func.sum(CostApiUsageLog.estimated_call_cost), 0).label("estimated_call_cost_total"),
    ).filter(CostApiUsageLog.provider == provider)
    if start_at is not None:
        q = q.filter(CostApiUsageLog.created_at >= start_at)
    if end_at is not None:
        q = q.filter(CostApiUsageLog.created_at < end_at)
    if org_id is not None:
        q = q.filter(CostApiUsageLog.org_id == org_id)
    if tenant_id is not None:
        q = q.filter(CostApiUsageLog.tenant_id == tenant_id)
    if cloud_account_id is not None:
        q = q.filter(CostApiUsageLog.cloud_account_id == cloud_account_id)
    rows = (
        q.group_by(group_col)
        .order_by(func.count(CostApiUsageLog.id).desc())
        .limit(limit)
        .all()
    )
    out: list[dict] = []
    for r in rows:
        out.append(
            {
                "group_key": str(r.group_key) if r.group_key is not None else "none",
                "total_calls": int(r.total_calls or 0),
                "live_calls": int(r.live_calls or 0),
                "cache_hits": int(r.cache_hits or 0),
                "estimated_call_cost_total": float(r.estimated_call_cost_total or 0),
            }
        )
    return out


def acquire_fetch_lock(
    db: Session,
    *,
    org_id: UUID,
    tenant_id: UUID,
    cloud_account_id: UUID,
    provider: str,
    request_signature: str,
    lock_reason: str,
    lock_seconds: int = 600,
) -> bool:
    row = db.query(CostFetchLock).filter(CostFetchLock.request_signature == request_signature).first()
    now = utcnow()
    if row is not None:
        if row.locked_until and row.locked_until > now:
            return False
        row.org_id = org_id
        row.tenant_id = tenant_id
        row.cloud_account_id = cloud_account_id
        row.provider = provider
        row.lock_reason = lock_reason
        row.locked_until = now + timedelta(seconds=lock_seconds)
        row.updated_at = now
        db.flush()
        return True
    row = CostFetchLock(
        org_id=org_id,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        provider=provider,
        request_signature=request_signature,
        lock_reason=lock_reason,
        locked_until=now + timedelta(seconds=lock_seconds),
    )
    db.add(row)
    db.flush()
    return True


def release_fetch_lock(db: Session, *, request_signature: str) -> None:
    row = db.query(CostFetchLock).filter(CostFetchLock.request_signature == request_signature).first()
    if row is None:
        return
    row.locked_until = utcnow()
    row.updated_at = utcnow()
    db.flush()

