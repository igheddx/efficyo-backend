"""Cost summary orchestration service for tenant-scoped cloud accounts."""

from uuid import UUID

from sqlalchemy.orm import Session

from app.core.cost_window import account_cost_window_fields
from app.services import cloud_account_service, cost_explorer_service


def get_cost_summary(db_session: Session, tenant_id: UUID, cloud_account_id: UUID) -> dict:
    """Return a normalized Cost Explorer summary for a tenant-scoped cloud account."""
    cloud_account = cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)
    raw = cost_explorer_service.fetch_cost_summary(role_arn=cloud_account.role_arn)
    return {**account_cost_window_fields(), **raw}


def get_ec2_other_breakdown(db_session: Session, tenant_id: UUID, cloud_account_id: UUID) -> dict:
    """Return EC2-Other cost breakdown for a tenant-scoped cloud account."""
    cloud_account = cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)
    raw = cost_explorer_service.fetch_ec2_other_breakdown(role_arn=cloud_account.role_arn)
    return {**account_cost_window_fields(), **raw}
