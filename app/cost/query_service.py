from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.cost.query import cost_query_service


def get_summary(db_session: Session, tenant_id: UUID, cloud_account_id: UUID) -> dict:
    return cost_query_service.get_summary(db_session, tenant_id=tenant_id, cloud_account_id=cloud_account_id)


def get_cost_summary(db_session: Session, tenant_id: UUID, cloud_account_id: UUID) -> dict:
    return get_summary(db_session, tenant_id, cloud_account_id)


def get_ec2_other_breakdown(db_session: Session, tenant_id: UUID, cloud_account_id: UUID) -> dict:
    return cost_query_service.get_ec2_other_breakdown(db_session, tenant_id=tenant_id, cloud_account_id=cloud_account_id)


def get_wow_trends(db_session: Session, tenant_id: UUID, cloud_account_id: UUID) -> list[dict]:
    return cost_query_service.get_trends(db_session, tenant_id=tenant_id, cloud_account_id=cloud_account_id)


def get_cost_trends(db_session: Session, tenant_id: UUID, cloud_account_id: UUID, days: int = 30) -> dict:
    return cost_query_service.get_cost_trends_over_time(
        db_session,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        days=days,
    )

