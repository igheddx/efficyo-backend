"""Resource snapshot persistence service."""

from datetime import datetime
import logging
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import utc_now
from app.models.resource_snapshot import ResourceSnapshot

logger = logging.getLogger(__name__)


def create_snapshots(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    resources: list[dict],
) -> tuple[int, datetime]:
    """
    Create ResourceSnapshot records for a list of resources.

    Args:
        db_session: Database session
        tenant_id: Tenant ID
        cloud_account_id: Cloud account ID
        resources: List of normalized resource dicts with keys:
                   resource_id, resource_type, region, configuration_json, tags_json

    Returns:
        Tuple of (count of inserted snapshots, captured_at timestamp)
    """
    if not resources:
        return 0, utc_now()

    captured_at = utc_now()
    snapshots = []

    for resource in resources:
        snapshot = ResourceSnapshot(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            resource_id=resource["resource_id"],
            resource_type=resource["resource_type"],
            region=resource["region"],
            configuration_json=resource["configuration_json"],
            tags_json=resource["tags_json"],
            captured_at=captured_at,
        )
        snapshots.append(snapshot)

    db_session.add_all(snapshots)
    try:
        db_session.commit()
    except IntegrityError:
        db_session.rollback()
        logger.exception(
            "Failed to persist resource snapshots due to integrity error",
            extra={"tenant_id": str(tenant_id), "cloud_account_id": str(cloud_account_id)},
        )
        raise
    except Exception:
        db_session.rollback()
        logger.exception(
            "Failed to persist resource snapshots",
            extra={"tenant_id": str(tenant_id), "cloud_account_id": str(cloud_account_id)},
        )
        raise

    return len(snapshots), captured_at
