"""AWS EC2 inventory service for fetching instance details."""

from __future__ import annotations

import logging
from collections.abc import Callable

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings
from app.services import aws_assume_role_service

logger = logging.getLogger(__name__)

_SKIPPABLE_REGION_ERRORS = frozenset({
    "UnauthorizedOperation",
    "AuthFailure",
    "AccessDenied",
    "AccessDeniedException",
})


def _inventory_regions(role_arn: str, home_region: str) -> list[str]:
    """Regions to scan: either the cloud account default only, or all commercial regions for the account."""
    if not settings.aws_scan_all_regions:
        return [home_region]
    try:
        ec2 = _create_assumed_client("ec2", role_arn=role_arn, region=home_region)
        resp = ec2.describe_regions(AllRegions=False)
        names = sorted({r["RegionName"] for r in resp.get("Regions", [])})
        return names if names else [home_region]
    except (ClientError, BotoCoreError) as exc:
        logger.warning(
            "describe_regions failed; using home region only for inventory",
            extra={"home_region": home_region, "error": str(exc)},
        )
        return [home_region]


def _gather_per_region(
    role_arn: str,
    home_region: str,
    fetch_one_region: Callable[[str], list[dict]],
    label: str,
) -> list[dict]:
    regions = _inventory_regions(role_arn, home_region)
    merged: list[dict] = []
    for reg in regions:
        try:
            merged.extend(fetch_one_region(reg))
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in _SKIPPABLE_REGION_ERRORS:
                logger.warning(
                    "Skipping %s inventory in region %s: %s",
                    label,
                    reg,
                    code,
                )
                continue
            raise
        except BotoCoreError as exc:
            logger.warning("Skipping %s inventory in region %s: %s", label, reg, exc)
            continue
    if settings.aws_scan_all_regions and len(regions) > 1:
        logger.info("Merged %d %s resources from %d regions", len(merged), label, len(regions))
    return merged


def _create_assumed_client(service_name: str, role_arn: str, region: str):
    """Create a boto3 client using assumed role credentials."""
    credentials = aws_assume_role_service.assume_role(
        role_arn=role_arn,
        region=region,
        session_name="fptnext-inventory",
    )
    return boto3.client(
        service_name,
        region_name=region,
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )


def _normalize_tags(tag_list: list[dict] | None) -> dict:
    """Normalize AWS tag list to key-value map."""
    tags: dict[str, str] = {}
    for tag in tag_list or []:
        key = tag.get("Key", "")
        if key:
            tags[key] = tag.get("Value", "")
    return tags


def _fetch_rds_tags_for_arn(rds_client, resource_arn: str | None, resource_id: str) -> dict:
    """Fetch tags for a single RDS/Aurora resource, returning empty tags on failure."""
    if not resource_arn:
        return {}

    try:
        response = rds_client.list_tags_for_resource(ResourceName=resource_arn)
        return _normalize_tags(response.get("TagList", []))
    except (ClientError, BotoCoreError) as exc:
        logger.warning(
            "Failed to fetch tags for RDS resource",
            extra={"resource_id": resource_id, "resource_arn": resource_arn, "error": str(exc)},
        )
        return {}
    except Exception as exc:
        logger.warning(
            "Unexpected error fetching tags for RDS resource",
            extra={"resource_id": resource_id, "resource_arn": resource_arn, "error": str(exc)},
        )
        return {}


def _fetch_lambda_tags_for_arn(lambda_client, function_arn: str | None, resource_id: str) -> dict:
    """Fetch tags for a Lambda function, returning empty tags on failure."""
    if not function_arn:
        return {}

    try:
        response = lambda_client.list_tags(Resource=function_arn)
        return response.get("Tags", {})
    except (ClientError, BotoCoreError) as exc:
        logger.warning(
            "Failed to fetch tags for Lambda function",
            extra={"resource_id": resource_id, "function_arn": function_arn, "error": str(exc)},
        )
        return {}
    except Exception as exc:
        logger.warning(
            "Unexpected error fetching tags for Lambda function",
            extra={"resource_id": resource_id, "function_arn": function_arn, "error": str(exc)},
        )
        return {}


def _fetch_ec2_instances_one_region(role_arn: str, region: str) -> list[dict]:
    ec2_client = _create_assumed_client("ec2", role_arn=role_arn, region=region)
    paginator = ec2_client.get_paginator("describe_instances")
    page_iterator = paginator.paginate()
    instances = []
    for page in page_iterator:
        for reservation in page.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                instances.append(_normalize_instance(instance, region))
    logger.info("Fetched %d EC2 instances from %s", len(instances), region)
    return instances


def fetch_ec2_instances(role_arn: str, region: str) -> list[dict]:
    """
    Fetch EC2 instances from an AWS account using role assumption.

    When ``FPTNEXT_AWS_SCAN_ALL_REGIONS`` is enabled, queries every commercial region
    returned by ``ec2:DescribeRegions``; otherwise only ``region`` (cloud default).

    Raises:
        ClientError: On AWS API errors (after optional per-region skips)
        BotoCoreError: On AWS SDK errors
    """
    try:

        def _one(reg: str) -> list[dict]:
            return _fetch_ec2_instances_one_region(role_arn, reg)

        return _gather_per_region(role_arn, region, _one, "EC2")
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        error_msg = exc.response["Error"]["Message"]
        logger.error("AWS API error fetching EC2 instances: %s - %s", error_code, error_msg)
        raise
    except BotoCoreError as exc:
        logger.error("BotoCore error fetching EC2 instances: %s", str(exc))
        raise
    except Exception as exc:
        logger.error("Unexpected error fetching EC2 instances: %s", str(exc))
        raise


def _fetch_rds_instances_one_region(role_arn: str, region: str) -> list[dict]:
    rds_client = _create_assumed_client("rds", role_arn=role_arn, region=region)
    paginator = rds_client.get_paginator("describe_db_instances")
    instances = []
    for page in paginator.paginate():
        for db_instance in page.get("DBInstances", []):
            resource_id = db_instance.get("DBInstanceIdentifier", "unknown")
            tags = _fetch_rds_tags_for_arn(
                rds_client,
                db_instance.get("DBInstanceArn"),
                resource_id,
            )
            instances.append(_normalize_rds_instance(db_instance, region, tags))
    logger.info("Fetched %d RDS DB instances from %s", len(instances), region)
    return instances


def fetch_rds_instances(role_arn: str, region: str) -> list[dict]:
    """Fetch RDS DB instances and normalize them to snapshot format (optionally all regions)."""
    try:

        def _one(reg: str) -> list[dict]:
            return _fetch_rds_instances_one_region(role_arn, reg)

        return _gather_per_region(role_arn, region, _one, "RDS")
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        error_msg = exc.response["Error"]["Message"]
        logger.error("AWS API error fetching RDS instances: %s - %s", error_code, error_msg)
        raise
    except BotoCoreError as exc:
        logger.error("BotoCore error fetching RDS instances: %s", str(exc))
        raise
    except Exception as exc:
        logger.error("Unexpected error fetching RDS instances: %s", str(exc))
        raise


def _fetch_aurora_clusters_one_region(role_arn: str, region: str) -> list[dict]:
    rds_client = _create_assumed_client("rds", role_arn=role_arn, region=region)
    paginator = rds_client.get_paginator("describe_db_clusters")
    clusters = []
    for page in paginator.paginate():
        for db_cluster in page.get("DBClusters", []):
            resource_id = db_cluster.get("DBClusterIdentifier", "unknown")
            tags = _fetch_rds_tags_for_arn(
                rds_client,
                db_cluster.get("DBClusterArn"),
                resource_id,
            )
            clusters.append(_normalize_aurora_cluster(db_cluster, region, tags))
    logger.info("Fetched %d Aurora DB clusters from %s", len(clusters), region)
    return clusters


def fetch_aurora_clusters(role_arn: str, region: str) -> list[dict]:
    """Fetch Aurora DB clusters and normalize them to snapshot format (optionally all regions)."""
    try:

        def _one(reg: str) -> list[dict]:
            return _fetch_aurora_clusters_one_region(role_arn, reg)

        return _gather_per_region(role_arn, region, _one, "Aurora")
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        error_msg = exc.response["Error"]["Message"]
        logger.error("AWS API error fetching Aurora clusters: %s - %s", error_code, error_msg)
        raise
    except BotoCoreError as exc:
        logger.error("BotoCore error fetching Aurora clusters: %s", str(exc))
        raise
    except Exception as exc:
        logger.error("Unexpected error fetching Aurora clusters: %s", str(exc))
        raise


def _fetch_lambda_functions_one_region(role_arn: str, region: str) -> list[dict]:
    lambda_client = _create_assumed_client("lambda", role_arn=role_arn, region=region)
    paginator = lambda_client.get_paginator("list_functions")
    functions = []
    for page in paginator.paginate():
        for function in page.get("Functions", []):
            resource_id = function.get("FunctionName", "unknown")
            tags = _fetch_lambda_tags_for_arn(
                lambda_client,
                function.get("FunctionArn"),
                resource_id,
            )
            functions.append(_normalize_lambda_function(function, region, tags))
    logger.info("Fetched %d Lambda functions from %s", len(functions), region)
    return functions


def fetch_lambda_functions(role_arn: str, region: str) -> list[dict]:
    """Fetch Lambda functions and normalize them to snapshot format (optionally all regions)."""
    try:

        def _one(reg: str) -> list[dict]:
            return _fetch_lambda_functions_one_region(role_arn, reg)

        return _gather_per_region(role_arn, region, _one, "Lambda")
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        error_msg = exc.response["Error"]["Message"]
        logger.error("AWS API error fetching Lambda functions: %s - %s", error_code, error_msg)
        raise
    except BotoCoreError as exc:
        logger.error("BotoCore error fetching Lambda functions: %s", str(exc))
        raise
    except Exception as exc:
        logger.error("Unexpected error fetching Lambda functions: %s", str(exc))
        raise


def _fetch_ebs_volumes_one_region(role_arn: str, region: str) -> list[dict]:
    ec2_client = _create_assumed_client("ec2", role_arn=role_arn, region=region)
    paginator = ec2_client.get_paginator("describe_volumes")
    volumes = []
    for page in paginator.paginate():
        for volume in page.get("Volumes", []):
            volumes.append(_normalize_ebs_volume(volume, region))
    logger.info("Fetched %d EBS volumes from %s", len(volumes), region)
    return volumes


def fetch_ebs_volumes(role_arn: str, region: str) -> list[dict]:
    """Fetch EBS volumes and normalize them to snapshot format (optionally all regions)."""
    try:

        def _one(reg: str) -> list[dict]:
            return _fetch_ebs_volumes_one_region(role_arn, reg)

        return _gather_per_region(role_arn, region, _one, "EBS")
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        error_msg = exc.response["Error"]["Message"]
        logger.error("AWS API error fetching EBS volumes: %s - %s", error_code, error_msg)
        raise
    except BotoCoreError as exc:
        logger.error("BotoCore error fetching EBS volumes: %s", str(exc))
        raise
    except Exception as exc:
        logger.error("Unexpected error fetching EBS volumes: %s", str(exc))
        raise


def _normalize_instance(instance: dict, region: str) -> dict:
    """
    Normalize an EC2 instance response into ResourceSnapshot format.

    Args:
        instance: Raw EC2 instance dict from describe_instances
        region: AWS region

    Returns:
        Normalized instance dict with resource_id, resource_type, region, configuration_json, tags_json
    """
    instance_id = instance.get("InstanceId", "unknown")
    
    # Extract configuration: minimal but deterministic
    configuration = {
        "instance_type": instance.get("InstanceType", ""),
        "state": instance.get("State", {}).get("Name", ""),
        "ami_id": instance.get("ImageId", ""),
        "availability_zone": instance.get("Placement", {}).get("AvailabilityZone", ""),
        "private_ip_address": instance.get("PrivateIpAddress"),
        "public_ip_address": instance.get("PublicIpAddress"),
        "vpc_id": instance.get("VpcId"),
        "subnet_id": instance.get("SubnetId"),
        "security_groups": [sg.get("GroupId") for sg in instance.get("SecurityGroups", [])],
    }
    
    tags = _normalize_tags(instance.get("Tags", []))

    return {
        "resource_id": instance_id,
        "resource_type": "ec2_instance",
        "region": region,
        "configuration_json": configuration,
        "tags_json": tags,
    }


def _normalize_rds_instance(db_instance: dict, region: str, tags: dict | None = None) -> dict:
    """Normalize an RDS DB instance to snapshot format."""
    configuration = {
        "engine": db_instance.get("Engine", ""),
        "db_instance_class": db_instance.get("DBInstanceClass", ""),
        "allocated_storage": db_instance.get("AllocatedStorage"),
        "storage_type": db_instance.get("StorageType", ""),
        "multi_az": db_instance.get("MultiAZ"),
        "publicly_accessible": db_instance.get("PubliclyAccessible"),
        "db_cluster_identifier": db_instance.get("DBClusterIdentifier"),
        "db_instance_status": db_instance.get("DBInstanceStatus", ""),
        "availability_zone": db_instance.get("AvailabilityZone", ""),
    }

    return {
        "resource_id": db_instance.get("DBInstanceIdentifier", "unknown"),
        "resource_type": "rds_instance",
        "region": region,
        "configuration_json": configuration,
        "tags_json": tags or {},
    }


def _normalize_aurora_cluster(db_cluster: dict, region: str, tags: dict | None = None) -> dict:
    """Normalize an Aurora DB cluster to snapshot format."""
    configuration = {
        "engine": db_cluster.get("Engine", ""),
        "engine_mode": db_cluster.get("EngineMode", ""),
        "status": db_cluster.get("Status", ""),
        "storage_encrypted": db_cluster.get("StorageEncrypted"),
        "endpoint": db_cluster.get("Endpoint", ""),
        "serverless_v2_scaling": db_cluster.get("ServerlessV2ScalingConfiguration"),
        "scaling_configuration": db_cluster.get("ScalingConfigurationInfo"),
    }

    return {
        "resource_id": db_cluster.get("DBClusterIdentifier", "unknown"),
        "resource_type": "aurora_cluster",
        "region": region,
        "configuration_json": configuration,
        "tags_json": tags or {},
    }


def _normalize_lambda_function(function: dict, region: str, tags: dict | None = None) -> dict:
    """Normalize a Lambda function to snapshot format."""
    configuration = {
        "runtime": function.get("Runtime", ""),
        "memory_size": function.get("MemorySize"),
        "timeout": function.get("Timeout"),
        "last_modified": function.get("LastModified", ""),
    }

    return {
        "resource_id": function.get("FunctionName", "unknown"),
        "resource_type": "lambda_function",
        "region": region,
        "configuration_json": configuration,
        "tags_json": tags or {},
    }


def _normalize_ebs_volume(volume: dict, region: str) -> dict:
    """Normalize an EBS volume to snapshot format."""
    configuration = {
        "size_gb": volume.get("Size"),
        "state": volume.get("State", ""),
        "attachments": [
            {
                "instance_id": attachment.get("InstanceId"),
                "state": attachment.get("State"),
                "device": attachment.get("Device"),
            }
            for attachment in volume.get("Attachments", [])
        ],
        "availability_zone": volume.get("AvailabilityZone", ""),
    }

    return {
        "resource_id": volume.get("VolumeId", "unknown"),
        "resource_type": "ebs_volume",
        "region": region,
        "configuration_json": configuration,
        "tags_json": _normalize_tags(volume.get("Tags", [])),
    }


def fetch_s3_buckets(role_arn: str, region: str) -> list[dict]:
    """Fetch S3 buckets and normalize them to snapshot format."""
    try:
        s3_client = _create_assumed_client("s3", role_arn=role_arn, region=region)

        response = s3_client.list_buckets()
        buckets = []
        for bucket in response.get("Buckets", []):
            bucket_name = bucket.get("Name", "unknown")
            tags = _fetch_s3_bucket_tags(s3_client, bucket_name)
            buckets.append(_normalize_s3_bucket(bucket, region, s3_client, tags))

        logger.info(f"Fetched {len(buckets)} S3 buckets")
        return buckets
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        error_msg = exc.response["Error"]["Message"]
        logger.error(f"AWS API error fetching S3 buckets: {error_code} - {error_msg}")
        raise
    except BotoCoreError as exc:
        logger.error(f"BotoCore error fetching S3 buckets: {str(exc)}")
        raise
    except Exception as exc:
        logger.error(f"Unexpected error fetching S3 buckets: {str(exc)}")
        raise


def _fetch_s3_bucket_tags(s3_client, bucket_name: str) -> dict:
    """Fetch tags for an S3 bucket, returning empty tags on failure."""
    try:
        response = s3_client.get_bucket_tagging(Bucket=bucket_name)
        tags = {}
        for tag_set in response.get("TagSet", []):
            key = tag_set.get("Key", "")
            if key:
                tags[key] = tag_set.get("Value", "")
        return tags
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        if error_code == "NoSuchTagSet":
            return {}
        elif error_code == "AccessDenied":
            logger.warning(
                "Failed to fetch tags for S3 bucket (access denied)",
                extra={"bucket_name": bucket_name},
            )
            return {}
        else:
            raise
    except BotoCoreError as exc:
        logger.warning(
            "BotoCore error fetching tags for S3 bucket",
            extra={"bucket_name": bucket_name, "error": str(exc)},
        )
        raise


def _normalize_s3_bucket(bucket: dict, region: str, s3_client, tags: dict | None = None) -> dict:
    """Normalize an S3 bucket to snapshot format."""
    bucket_name = bucket.get("Name", "unknown")

    versioning_status = "unknown"
    encryption_enabled = False
    public_access_block_status = {}
    lifecycle_rules_count = 0

    try:
        versioning_response = s3_client.get_bucket_versioning(Bucket=bucket_name)
        versioning_status = versioning_response.get("Status", "disabled")
    except Exception:
        pass

    try:
        encryption_response = s3_client.get_bucket_encryption(Bucket=bucket_name)
        encryption_enabled = "Rules" in encryption_response
    except Exception:
        pass

    try:
        pab_response = s3_client.get_public_access_block(Bucket=bucket_name)
        pab_config = pab_response.get("PublicAccessBlockConfiguration", {})
        public_access_block_status = {
            "block_public_acls": pab_config.get("BlockPublicAcls", False),
            "ignore_public_acls": pab_config.get("IgnorePublicAcls", False),
            "block_public_policy": pab_config.get("BlockPublicPolicy", False),
            "restrict_public_buckets": pab_config.get("RestrictPublicBuckets", False),
        }
    except Exception:
        pass

    try:
        lifecycle_response = s3_client.get_bucket_lifecycle_configuration(Bucket=bucket_name)
        lifecycle_rules_count = len(lifecycle_response.get("Rules", []))
    except Exception:
        pass

    configuration = {
        "versioning_status": versioning_status,
        "encryption_enabled": encryption_enabled,
        "public_access_block_status": public_access_block_status if public_access_block_status else None,
        "lifecycle_rules_count": lifecycle_rules_count,
    }

    return {
        "resource_id": bucket_name,
        "resource_type": "s3_bucket",
        "region": region,
        "configuration_json": configuration,
        "tags_json": tags or {},
    }
