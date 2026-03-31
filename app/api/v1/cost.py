from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.user_context import UserContext, get_user_context
from app.cost import repository as cost_repository
from app.cost.query import cost_query_service
from app.schemas.cost import (
    CostFreshnessRead,
    CostUsageGroupItemRead,
    CostUsageGroupRead,
    CostUsageItemRead,
    CostUsageRead,
    CostUsageSummaryRead,
)
from app.services import tenant_scope_service

router = APIRouter(prefix="/cost", tags=["cost"])


def _ensure_access(db: Session, ctx: UserContext, tenant_id: UUID) -> None:
    tenant_scope_service.require_tenant_accessible(db, ctx, tenant_id)


def _parse_iso_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


@router.get("/summary", status_code=status.HTTP_200_OK)
def get_cost_summary(
    tenant_id: UUID = Query(...),
    cloud_account_id: UUID = Query(...),
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> dict[str, Any]:
    _ensure_access(db, ctx, tenant_id)
    return cost_query_service.get_summary(db, tenant_id=tenant_id, cloud_account_id=cloud_account_id)


@router.get("/trends", status_code=status.HTTP_200_OK)
def get_cost_trends(
    tenant_id: UUID = Query(...),
    cloud_account_id: UUID = Query(...),
    days: int = Query(30, ge=1, le=180),
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> dict[str, Any]:
    _ensure_access(db, ctx, tenant_id)
    return cost_query_service.get_cost_trends_over_time(
        db,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        days=days,
    )


@router.get("/snapshots/latest", status_code=status.HTTP_200_OK)
def get_latest_snapshot(
    tenant_id: UUID = Query(...),
    cloud_account_id: UUID = Query(...),
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> dict[str, Any]:
    _ensure_access(db, ctx, tenant_id)
    return {
        "summary": cost_query_service.get_summary(db, tenant_id=tenant_id, cloud_account_id=cloud_account_id),
        "ec2_other_breakdown": cost_query_service.get_ec2_other_breakdown(
            db, tenant_id=tenant_id, cloud_account_id=cloud_account_id
        ),
    }


@router.get("/usage", response_model=CostUsageRead, status_code=status.HTTP_200_OK)
def get_cost_usage(
    tenant_id: UUID = Query(...),
    cloud_account_id: UUID = Query(...),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> CostUsageRead:
    _ensure_access(db, ctx, tenant_id)
    rows, total = cost_repository.list_usage_logs(
        db,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        limit=limit,
    )
    return CostUsageRead(
        total=total,
        items=[
            CostUsageItemRead(
                feature_name=r.feature_name,
                request_type=r.request_type,
                api_name=r.api_name,
                was_cache_hit=r.was_cache_hit,
                created_at=r.created_at,
            )
            for r in rows
        ],
    )


@router.get("/freshness", response_model=CostFreshnessRead, status_code=status.HTTP_200_OK)
def get_cost_freshness(
    tenant_id: UUID = Query(...),
    cloud_account_id: UUID = Query(...),
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> CostFreshnessRead:
    _ensure_access(db, ctx, tenant_id)
    f = cost_query_service.get_freshness(db, tenant_id=tenant_id, cloud_account_id=cloud_account_id)
    return CostFreshnessRead(
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        is_snapshot_missing=bool(f["is_snapshot_missing"]),
        is_stale=bool(f["is_stale"]),
        last_updated_at=f.get("last_updated_at"),
        stale_after_minutes=f.get("stale_after_minutes"),
    )


@router.get("/usage/summary", response_model=CostUsageSummaryRead, status_code=status.HTTP_200_OK)
def get_cost_usage_summary(
    tenant_id: UUID = Query(...),
    cloud_account_id: UUID = Query(...),
    start_at: str | None = Query(None, description="ISO8601 inclusive lower bound"),
    end_at: str | None = Query(None, description="ISO8601 exclusive upper bound"),
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> CostUsageSummaryRead:
    _ensure_access(db, ctx, tenant_id)
    s = _parse_iso_dt(start_at)
    e = _parse_iso_dt(end_at)
    summary = cost_repository.summarize_usage(
        db,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        start_at=s,
        end_at=e,
    )
    return CostUsageSummaryRead(start_at=start_at, end_at=end_at, **summary)


def _usage_group_response(items: list[dict], start_at: str | None, end_at: str | None) -> CostUsageGroupRead:
    return CostUsageGroupRead(
        start_at=start_at,
        end_at=end_at,
        items=[CostUsageGroupItemRead(**item) for item in items],
    )


@router.get("/usage/by-account", response_model=CostUsageGroupRead, status_code=status.HTTP_200_OK)
def get_cost_usage_by_account(
    tenant_id: UUID = Query(...),
    start_at: str | None = Query(None),
    end_at: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> CostUsageGroupRead:
    _ensure_access(db, ctx, tenant_id)
    items = cost_repository.aggregate_usage(
        db,
        group_by="account",
        tenant_id=tenant_id,
        start_at=_parse_iso_dt(start_at),
        end_at=_parse_iso_dt(end_at),
        limit=limit,
    )
    return _usage_group_response(items, start_at, end_at)


@router.get("/usage/by-job", response_model=CostUsageGroupRead, status_code=status.HTTP_200_OK)
def get_cost_usage_by_job(
    tenant_id: UUID = Query(...),
    cloud_account_id: UUID = Query(...),
    start_at: str | None = Query(None),
    end_at: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> CostUsageGroupRead:
    _ensure_access(db, ctx, tenant_id)
    items = cost_repository.aggregate_usage(
        db,
        group_by="job",
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        start_at=_parse_iso_dt(start_at),
        end_at=_parse_iso_dt(end_at),
        limit=limit,
    )
    return _usage_group_response(items, start_at, end_at)


@router.get("/usage/by-feature", response_model=CostUsageGroupRead, status_code=status.HTTP_200_OK)
def get_cost_usage_by_feature(
    tenant_id: UUID = Query(...),
    cloud_account_id: UUID = Query(...),
    start_at: str | None = Query(None),
    end_at: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> CostUsageGroupRead:
    _ensure_access(db, ctx, tenant_id)
    items = cost_repository.aggregate_usage(
        db,
        group_by="feature",
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        start_at=_parse_iso_dt(start_at),
        end_at=_parse_iso_dt(end_at),
        limit=limit,
    )
    return _usage_group_response(items, start_at, end_at)


@router.get("/usage/by-org", response_model=CostUsageGroupRead, status_code=status.HTTP_200_OK)
def get_cost_usage_by_org(
    org_id: UUID | None = Query(None),
    start_at: str | None = Query(None),
    end_at: str | None = Query(None),
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> CostUsageGroupRead:
    current_org_id = tenant_scope_service.require_data_access_organization_id(db, ctx)
    if org_id is not None and org_id != current_org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed for that organization.")
    items = cost_repository.aggregate_usage(
        db,
        group_by="org",
        org_id=current_org_id,
        start_at=_parse_iso_dt(start_at),
        end_at=_parse_iso_dt(end_at),
        limit=50,
    )
    return _usage_group_response(items, start_at, end_at)

