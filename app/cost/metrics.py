from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from app.cost import repository
from app.cost.models import CostRequestContext

logger = logging.getLogger(__name__)


def log_cost_api_call(
    db: Session,
    *,
    ctx: CostRequestContext,
    request_signature: str,
    was_cache_hit: bool,
    api_name: str,
    estimated_call_cost: Decimal = Decimal("0"),
) -> None:
    repository.log_api_usage(
        db,
        org_id=ctx.org_id,
        tenant_id=ctx.tenant_id,
        cloud_account_id=ctx.cloud_account_id,
        provider=ctx.provider,
        sync_job_id=ctx.sync_job_id,
        feature_name=ctx.feature_name,
        request_type=ctx.request_type,
        request_signature=request_signature,
        was_cache_hit=was_cache_hit,
        api_name=api_name,
        estimated_call_cost=estimated_call_cost,
    )


def log_cost_data_event(
    *,
    event: str,
    tenant_id,
    cloud_account_id,
    org_id=None,
    details: dict | None = None,
) -> None:
    logger.info(
        "cost_data_event",
        extra={
            "event": event,
            "org_id": str(org_id) if org_id is not None else None,
            "tenant_id": str(tenant_id),
            "cloud_account_id": str(cloud_account_id),
            "details": details or {},
        },
    )

