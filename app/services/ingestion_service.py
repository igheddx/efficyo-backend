"""Ingestion orchestration service for EC2 inventory."""

from dataclasses import dataclass
from datetime import datetime
import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.services import aws_extended_inventory, aws_inventory_service, cloud_account_service, resource_snapshot_service

logger = logging.getLogger(__name__)


def _enrich_aurora_clusters_with_member_public_access(
    rds_instances: list[dict],
    aurora_clusters: list[dict],
) -> None:
    """
    DescribeDBClusters does not expose PubliclyAccessible; copy it from cluster member instances
    so detection can raise rds_publicly_accessible on aurora_cluster rows.
    """
    for cluster in aurora_clusters:
        cid = cluster.get("resource_id")
        if not cid:
            continue
        cfg = cluster.setdefault("configuration_json", {})
        members_public = False
        has_member = False
        for inst in rds_instances:
            icfg = inst.get("configuration_json") or {}
            if icfg.get("db_cluster_identifier") != cid:
                continue
            has_member = True
            if icfg.get("publicly_accessible") is True:
                members_public = True
                break
        if members_public:
            cfg["publicly_accessible"] = True
        elif has_member:
            cfg["publicly_accessible"] = False


@dataclass
class IngestionResult:
    """Result of an ingestion run."""

    cloud_account_id: UUID
    resource_type: str
    ingested_count: int
    captured_at: datetime


def ingest_ec2(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> IngestionResult:
    """
    Ingest EC2 instances from a cloud account and store as ResourceSnapshots.

    Args:
        db_session: Database session
        tenant_id: Tenant ID
        cloud_account_id: Cloud account ID

    Returns:
        IngestionResult with counts and timestamp

    Raises:
        ValueError: If tenant or cloud account not found
        ClientError: On AWS API errors
    """
    cloud_account = cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)

    try:
        # Fetch EC2 inventory from AWS
        instances = aws_inventory_service.fetch_ec2_instances(
            role_arn=cloud_account.role_arn,
            region=cloud_account.region_default,
        )

        # Persist snapshots
        ingested_count, captured_at = resource_snapshot_service.create_snapshots(
            db_session,
            tenant_id,
            cloud_account_id,
            instances,
        )
    except Exception:
        logger.exception(
            "EC2 ingestion failed",
            extra={
                "tenant_id": str(tenant_id),
                "cloud_account_id": str(cloud_account_id),
                "region": cloud_account.region_default,
            },
        )
        raise

    return IngestionResult(
        cloud_account_id=cloud_account_id,
        resource_type="ec2_instance",
        ingested_count=ingested_count,
        captured_at=captured_at,
    )


def ingest_rds(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> IngestionResult:
    """Ingest RDS instances and Aurora clusters into ResourceSnapshots."""
    cloud_account = cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)

    try:
        rds_instances = aws_inventory_service.fetch_rds_instances(
            role_arn=cloud_account.role_arn,
            region=cloud_account.region_default,
        )
        aurora_clusters = aws_inventory_service.fetch_aurora_clusters(
            role_arn=cloud_account.role_arn,
            region=cloud_account.region_default,
        )
        _enrich_aurora_clusters_with_member_public_access(rds_instances, aurora_clusters)
        resources = rds_instances + aurora_clusters

        ingested_count, captured_at = resource_snapshot_service.create_snapshots(
            db_session,
            tenant_id,
            cloud_account_id,
            resources,
        )
    except Exception:
        logger.exception(
            "RDS ingestion failed",
            extra={
                "tenant_id": str(tenant_id),
                "cloud_account_id": str(cloud_account_id),
                "region": cloud_account.region_default,
            },
        )
        raise

    return IngestionResult(
        cloud_account_id=cloud_account_id,
        resource_type="rds",
        ingested_count=ingested_count,
        captured_at=captured_at,
    )


def ingest_lambda(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> IngestionResult:
    """Ingest Lambda functions into ResourceSnapshots."""
    cloud_account = cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)

    try:
        functions = aws_inventory_service.fetch_lambda_functions(
            role_arn=cloud_account.role_arn,
            region=cloud_account.region_default,
        )

        ingested_count, captured_at = resource_snapshot_service.create_snapshots(
            db_session,
            tenant_id,
            cloud_account_id,
            functions,
        )
    except Exception:
        logger.exception(
            "Lambda ingestion failed",
            extra={
                "tenant_id": str(tenant_id),
                "cloud_account_id": str(cloud_account_id),
                "region": cloud_account.region_default,
            },
        )
        raise

    return IngestionResult(
        cloud_account_id=cloud_account_id,
        resource_type="lambda_function",
        ingested_count=ingested_count,
        captured_at=captured_at,
    )


def ingest_s3(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> IngestionResult:
    """Ingest S3 buckets into ResourceSnapshots."""
    cloud_account = cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)

    try:
        buckets = aws_inventory_service.fetch_s3_buckets(
            role_arn=cloud_account.role_arn,
            region=cloud_account.region_default,
        )

        ingested_count, captured_at = resource_snapshot_service.create_snapshots(
            db_session,
            tenant_id,
            cloud_account_id,
            buckets,
        )
    except Exception:
        logger.exception(
            "S3 ingestion failed",
            extra={
                "tenant_id": str(tenant_id),
                "cloud_account_id": str(cloud_account_id),
                "region": cloud_account.region_default,
            },
        )
        raise

    return IngestionResult(
        cloud_account_id=cloud_account_id,
        resource_type="s3_bucket",
        ingested_count=ingested_count,
        captured_at=captured_at,
    )


def ingest_ebs(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> IngestionResult:
    """Ingest EBS volumes into ResourceSnapshots."""
    cloud_account = cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)

    try:
        volumes = aws_inventory_service.fetch_ebs_volumes(
            role_arn=cloud_account.role_arn,
            region=cloud_account.region_default,
        )

        ingested_count, captured_at = resource_snapshot_service.create_snapshots(
            db_session,
            tenant_id,
            cloud_account_id,
            volumes,
        )
    except Exception:
        logger.exception(
            "EBS ingestion failed",
            extra={
                "tenant_id": str(tenant_id),
                "cloud_account_id": str(cloud_account_id),
                "region": cloud_account.region_default,
            },
        )
        raise

    return IngestionResult(
        cloud_account_id=cloud_account_id,
        resource_type="ebs_volume",
        ingested_count=ingested_count,
        captured_at=captured_at,
    )


def ingest_extended_aws_services(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> dict[str, int]:
    """
    Ingest extended AWS services (CloudFront, ACM, API Gateway, EventBridge, SES, VPC components).

    Returns per-batch counts keyed by logical name (not necessarily ``resource_type``).
    """
    cloud_account = cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)
    role_arn = cloud_account.role_arn
    region = cloud_account.region_default

    counts: dict[str, int] = {}
    batches = aws_extended_inventory.fetch_all_extended(role_arn, region)
    for key, resources in batches.items():
        if not resources:
            counts[key] = 0
            continue
        try:
            n, _ = resource_snapshot_service.create_snapshots(
                db_session,
                tenant_id,
                cloud_account_id,
                resources,
            )
            counts[key] = n
        except Exception:
            logger.exception(
                "Extended AWS inventory batch failed",
                extra={
                    "tenant_id": str(tenant_id),
                    "cloud_account_id": str(cloud_account_id),
                    "batch": key,
                },
            )
            raise

    return counts
