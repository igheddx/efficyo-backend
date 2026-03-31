"""Cost summary orchestration service for tenant-scoped cloud accounts."""

from uuid import UUID

from sqlalchemy.orm import Session

from app.cost.query import cost_query_service
from app.services import cloud_account_service


def get_cost_summary(db_session: Session, tenant_id: UUID, cloud_account_id: UUID) -> dict:
    """Return snapshot-backed cost summary (never live CE for UI requests)."""
    cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)
    return cost_query_service.get_summary(db_session, tenant_id=tenant_id, cloud_account_id=cloud_account_id)


def get_ec2_other_breakdown(db_session: Session, tenant_id: UUID, cloud_account_id: UUID) -> dict:
    """Return snapshot-backed EC2-Other breakdown (never live CE for UI requests)."""
    cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)
    return cost_query_service.get_ec2_other_breakdown(
        db_session,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
    )
