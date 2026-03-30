"""Tests for EC2 inventory ingestion."""

from datetime import datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import status
from sqlalchemy.orm import Session

from app.models.cloud_account import CloudAccount
from app.models.resource_snapshot import ResourceSnapshot
from app.models.tenant import Tenant
from app.services import aws_inventory_service, ingestion_service, resource_snapshot_service


def test_enrich_aurora_clusters_with_member_public_access():
    clusters = [
        {
            "resource_id": "aurora-main",
            "resource_type": "aurora_cluster",
            "region": "us-east-1",
            "configuration_json": {"engine": "aurora-postgresql"},
            "tags_json": {},
        }
    ]
    instances = [
        {
            "resource_id": "writer-1",
            "resource_type": "rds_instance",
            "region": "us-east-1",
            "configuration_json": {
                "db_cluster_identifier": "aurora-main",
                "publicly_accessible": True,
            },
            "tags_json": {},
        }
    ]
    ingestion_service._enrich_aurora_clusters_with_member_public_access(instances, clusters)
    assert clusters[0]["configuration_json"]["publicly_accessible"] is True


class TestAwsInventoryService:
    """Test AWS EC2 inventory fetching."""

    @patch("app.services.aws_inventory_service.aws_assume_role_service.assume_role")
    @patch("app.services.aws_inventory_service.boto3.client")
    def test_fetch_ec2_instances_success(self, mock_boto3_client, mock_assume_role):
        """Test successful EC2 instance fetch."""
        # Mock assume_role
        mock_assume_role.return_value = {
            "AccessKeyId": "ASIAIOSFODNN7EXAMPLE",
            "SecretAccessKey": "example_secret",
            "SessionToken": "example_token",
        }

        # Mock EC2 client and paginator
        mock_ec2_client = MagicMock()
        mock_boto3_client.return_value = mock_ec2_client

        mock_paginator = MagicMock()
        mock_ec2_client.get_paginator.return_value = mock_paginator

        mock_paginator.paginate.return_value = [
            {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": "i-0123456789abcdef0",
                                "InstanceType": "t3.micro",
                                "State": {"Name": "running"},
                                "ImageId": "ami-0c55b159cbfafe1f0",
                                "Placement": {"AvailabilityZone": "us-east-1a"},
                                "PrivateIpAddress": "10.0.0.1",
                                "PublicIpAddress": "54.123.45.67",
                                "VpcId": "vpc-1234567890abcdef0",
                                "SubnetId": "subnet-1234567890abcdef0",
                                "SecurityGroups": [{"GroupId": "sg-12345678"}],
                                "Tags": [
                                    {"Key": "Name", "Value": "web-server"},
                                    {"Key": "Environment", "Value": "production"},
                                ],
                            }
                        ]
                    }
                ]
            }
        ]

        instances = aws_inventory_service.fetch_ec2_instances(
            role_arn="arn:aws:iam::123456789012:role/fptnext-validator",
            region="us-east-1",
        )

        assert len(instances) == 1
        assert instances[0]["resource_id"] == "i-0123456789abcdef0"
        assert instances[0]["resource_type"] == "ec2_instance"
        assert instances[0]["region"] == "us-east-1"
        assert instances[0]["configuration_json"]["instance_type"] == "t3.micro"
        assert instances[0]["tags_json"]["Name"] == "web-server"

    @patch("app.services.aws_inventory_service.aws_assume_role_service.assume_role")
    @patch("app.services.aws_inventory_service.boto3.client")
    def test_fetch_ec2_instances_empty(self, mock_boto3_client, mock_assume_role):
        """Test EC2 fetch with no instances."""
        mock_assume_role.return_value = {
            "AccessKeyId": "ASIAIOSFODNN7EXAMPLE",
            "SecretAccessKey": "example_secret",
            "SessionToken": "example_token",
        }

        mock_ec2_client = MagicMock()
        mock_boto3_client.return_value = mock_ec2_client

        mock_paginator = MagicMock()
        mock_ec2_client.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{"Reservations": []}]

        instances = aws_inventory_service.fetch_ec2_instances(
            role_arn="arn:aws:iam::123456789012:role/fptnext-validator",
            region="us-east-1",
        )

        assert len(instances) == 0

    def test_normalize_instance(self):
        """Test instance normalization."""
        instance = {
            "InstanceId": "i-0123456789abcdef0",
            "InstanceType": "t3.micro",
            "State": {"Name": "running"},
            "ImageId": "ami-0c55b159cbfafe1f0",
            "Placement": {"AvailabilityZone": "us-east-1a"},
            "PrivateIpAddress": "10.0.0.1",
            "PublicIpAddress": "54.123.45.67",
            "VpcId": "vpc-1234567890abcdef0",
            "SubnetId": "subnet-1234567890abcdef0",
            "SecurityGroups": [{"GroupId": "sg-12345678"}],
            "Tags": [{"Key": "Name", "Value": "web-server"}],
        }

        normalized = aws_inventory_service._normalize_instance(instance, "us-east-1")

        assert normalized["resource_id"] == "i-0123456789abcdef0"
        assert normalized["resource_type"] == "ec2_instance"
        assert normalized["region"] == "us-east-1"
        assert normalized["configuration_json"]["instance_type"] == "t3.micro"
        assert normalized["configuration_json"]["state"] == "running"
        assert normalized["tags_json"]["Name"] == "web-server"

    def test_normalize_rds_instance(self):
        """Test RDS instance normalization."""
        db_instance = {
            "DBInstanceIdentifier": "app-db-1",
            "Engine": "postgres",
            "DBInstanceClass": "db.t4g.medium",
            "AllocatedStorage": 100,
            "StorageType": "gp3",
            "MultiAZ": True,
            "PubliclyAccessible": False,
            "DBInstanceStatus": "available",
            "AvailabilityZone": "us-east-1a",
            "TagList": [{"Key": "Name", "Value": "primary-db"}],
        }

        normalized = aws_inventory_service._normalize_rds_instance(
            db_instance,
            "us-east-1",
            tags={"Name": "primary-db"},
        )

        assert normalized["resource_id"] == "app-db-1"
        assert normalized["resource_type"] == "rds_instance"
        assert normalized["region"] == "us-east-1"
        assert normalized["configuration_json"]["engine"] == "postgres"
        assert normalized["configuration_json"]["multi_az"] is True
        assert normalized["configuration_json"].get("db_cluster_identifier") is None
        assert normalized["tags_json"]["Name"] == "primary-db"

    def test_normalize_rds_instance_aurora_member_includes_cluster_id(self):
        db_instance = {
            "DBInstanceIdentifier": "aurora-writer",
            "Engine": "aurora-postgresql",
            "DBInstanceClass": "db.serverless",
            "AllocatedStorage": 1,
            "StorageType": "aurora",
            "MultiAZ": False,
            "PubliclyAccessible": True,
            "DBInstanceStatus": "available",
            "AvailabilityZone": "us-east-1a",
            "DBClusterIdentifier": "aurora-main",
        }
        normalized = aws_inventory_service._normalize_rds_instance(db_instance, "us-east-1", tags={})
        assert normalized["configuration_json"]["db_cluster_identifier"] == "aurora-main"
        assert normalized["configuration_json"]["publicly_accessible"] is True

    def test_normalize_aurora_cluster(self):
        """Test Aurora cluster normalization."""
        db_cluster = {
            "DBClusterIdentifier": "aurora-main",
            "Engine": "aurora-postgresql",
            "EngineMode": "provisioned",
            "Status": "available",
            "StorageEncrypted": True,
            "Endpoint": "aurora-main.cluster-xyz.us-east-1.rds.amazonaws.com",
            "TagList": [{"Key": "Env", "Value": "prod"}],
        }

        normalized = aws_inventory_service._normalize_aurora_cluster(
            db_cluster,
            "us-east-1",
            tags={"Env": "prod"},
        )

        assert normalized["resource_id"] == "aurora-main"
        assert normalized["resource_type"] == "aurora_cluster"
        assert normalized["region"] == "us-east-1"
        assert normalized["configuration_json"]["engine"] == "aurora-postgresql"
        assert normalized["configuration_json"]["storage_encrypted"] is True
        assert normalized["tags_json"]["Env"] == "prod"

    @patch("app.services.aws_inventory_service.aws_assume_role_service.assume_role")
    @patch("app.services.aws_inventory_service.boto3.client")
    def test_fetch_ebs_volumes_success(self, mock_boto3_client, mock_assume_role):
        """Test successful EBS volume fetch."""
        mock_assume_role.return_value = {
            "AccessKeyId": "ASIAIOSFODNN7EXAMPLE",
            "SecretAccessKey": "example_secret",
            "SessionToken": "example_token",
        }

        mock_ec2_client = MagicMock()
        mock_boto3_client.return_value = mock_ec2_client

        mock_paginator = MagicMock()
        mock_ec2_client.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            {
                "Volumes": [
                    {
                        "VolumeId": "vol-0123456789abcdef0",
                        "Size": 100,
                        "State": "available",
                        "AvailabilityZone": "us-east-1a",
                        "Attachments": [],
                        "Tags": [{"Key": "Name", "Value": "data-volume"}],
                    }
                ]
            }
        ]

        volumes = aws_inventory_service.fetch_ebs_volumes(
            role_arn="arn:aws:iam::123456789012:role/fptnext-validator",
            region="us-east-1",
        )

        assert len(volumes) == 1
        assert volumes[0]["resource_id"] == "vol-0123456789abcdef0"
        assert volumes[0]["resource_type"] == "ebs_volume"
        assert volumes[0]["configuration_json"]["size_gb"] == 100
        assert volumes[0]["configuration_json"]["state"] == "available"

    def test_normalize_ebs_volume(self):
        """Test EBS volume normalization."""
        volume = {
            "VolumeId": "vol-0123456789abcdef0",
            "Size": 50,
            "State": "in-use",
            "AvailabilityZone": "us-east-1a",
            "Attachments": [
                {"InstanceId": "i-0123456789abcdef0", "State": "attached", "Device": "/dev/xvda"}
            ],
            "Tags": [{"Key": "Name", "Value": "root-volume"}],
        }

        normalized = aws_inventory_service._normalize_ebs_volume(volume, "us-east-1")

        assert normalized["resource_id"] == "vol-0123456789abcdef0"
        assert normalized["resource_type"] == "ebs_volume"
        assert normalized["configuration_json"]["size_gb"] == 50
        assert normalized["configuration_json"]["state"] == "in-use"
        assert normalized["configuration_json"]["attachments"][0]["instance_id"] == "i-0123456789abcdef0"
        assert normalized["tags_json"]["Name"] == "root-volume"


class TestResourceSnapshotService:
    """Test resource snapshot persistence."""

    def test_create_snapshots_success(self, db: Session):
        """Test successful snapshot creation."""
        tenant = Tenant(name="test-tenant", status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        cloud_account = CloudAccount(
            tenant_id=tenant.id,
            account_id="123456789012",
            name="test-account",
            status="connected",
            role_arn="arn:aws:iam::123456789012:role/fptnext-validator",
            region_default="us-east-1",
        )
        db.add(cloud_account)
        db.commit()
        db.refresh(cloud_account)

        resources = [
            {
                "resource_id": "i-0123456789abcdef0",
                "resource_type": "ec2_instance",
                "region": "us-east-1",
                "configuration_json": {"instance_type": "t3.micro", "state": "running"},
                "tags_json": {"Name": "web-server"},
            },
            {
                "resource_id": "i-0123456789abcdef1",
                "resource_type": "ec2_instance",
                "region": "us-east-1",
                "configuration_json": {"instance_type": "t3.small", "state": "running"},
                "tags_json": {"Name": "db-server"},
            },
        ]

        count, captured_at = resource_snapshot_service.create_snapshots(
            db, tenant.id, cloud_account.id, resources
        )

        assert count == 2
        assert captured_at is not None

        # Verify snapshots were created
        snapshots = db.query(ResourceSnapshot).filter(
            ResourceSnapshot.tenant_id == tenant.id,
            ResourceSnapshot.cloud_account_id == cloud_account.id,
        ).all()

        assert len(snapshots) == 2
        assert snapshots[0].resource_id == "i-0123456789abcdef0"
        assert snapshots[1].resource_id == "i-0123456789abcdef1"

    def test_create_snapshots_empty(self, db: Session):
        """Test creating snapshots with empty list."""
        tenant = Tenant(name="test-tenant2", status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        count, captured_at = resource_snapshot_service.create_snapshots(
            db, tenant.id, uuid4(), []
        )

        assert count == 0
        assert captured_at is not None


class TestIngestionService:
    """Test ingestion orchestration."""

    @patch("app.services.aws_inventory_service.fetch_ec2_instances")
    def test_ingest_ec2_success(self, mock_fetch, db: Session):
        """Test successful EC2 ingestion."""
        tenant = Tenant(name="test-tenant3", status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        cloud_account = CloudAccount(
            tenant_id=tenant.id,
            account_id="123456789012",
            name="test-account",
            status="connected",
            role_arn="arn:aws:iam::123456789012:role/fptnext-validator",
            region_default="us-east-1",
        )
        db.add(cloud_account)
        db.commit()
        db.refresh(cloud_account)

        mock_fetch.return_value = [
            {
                "resource_id": "i-0123456789abcdef0",
                "resource_type": "ec2_instance",
                "region": "us-east-1",
                "configuration_json": {"instance_type": "t3.micro"},
                "tags_json": {"Name": "web-server"},
            }
        ]

        result = ingestion_service.ingest_ec2(db, tenant.id, cloud_account.id)

        assert result.cloud_account_id == cloud_account.id
        assert result.resource_type == "ec2_instance"
        assert result.ingested_count == 1
        assert result.captured_at is not None

    def test_ingest_ec2_tenant_not_found(self, db: Session):
        """Test ingestion with missing tenant."""
        fake_tenant_id = uuid4()
        fake_account_id = uuid4()

        with pytest.raises(ValueError, match="tenant_not_found"):
            ingestion_service.ingest_ec2(db, fake_tenant_id, fake_account_id)

    def test_ingest_ec2_account_not_found(self, db: Session):
        """Test ingestion with missing cloud account."""
        tenant = Tenant(name="test-tenant4", status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        fake_account_id = uuid4()

        with pytest.raises(ValueError, match="cloud_account_not_found"):
            ingestion_service.ingest_ec2(db, tenant.id, fake_account_id)

    @patch("app.services.aws_inventory_service.fetch_rds_instances")
    @patch("app.services.aws_inventory_service.fetch_aurora_clusters")
    def test_ingest_rds_success(self, mock_fetch_clusters, mock_fetch_instances, db: Session):
        """Test successful RDS ingestion."""
        tenant = Tenant(name="test-tenant-rds", status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        cloud_account = CloudAccount(
            tenant_id=tenant.id,
            account_id="123456789012",
            name="test-account-rds",
            status="connected",
            role_arn="arn:aws:iam::123456789012:role/fptnext-validator",
            region_default="us-east-1",
        )
        db.add(cloud_account)
        db.commit()
        db.refresh(cloud_account)

        mock_fetch_instances.return_value = [
            {
                "resource_id": "app-db-1",
                "resource_type": "rds_instance",
                "region": "us-east-1",
                "configuration_json": {"engine": "postgres"},
                "tags_json": {"Name": "primary-db"},
            }
        ]
        mock_fetch_clusters.return_value = [
            {
                "resource_id": "aurora-main",
                "resource_type": "aurora_cluster",
                "region": "us-east-1",
                "configuration_json": {"engine": "aurora-postgresql"},
                "tags_json": {"Env": "prod"},
            }
        ]

        result = ingestion_service.ingest_rds(db, tenant.id, cloud_account.id)

        assert result.cloud_account_id == cloud_account.id
        assert result.resource_type == "rds"
        assert result.ingested_count == 2
        assert result.captured_at is not None

    @patch("app.services.aws_inventory_service.fetch_ebs_volumes")
    def test_ingest_ebs_success(self, mock_fetch, db: Session):
        """Test successful EBS ingestion."""
        tenant = Tenant(name="test-tenant-ebs", status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        cloud_account = CloudAccount(
            tenant_id=tenant.id,
            account_id="123456789012",
            name="test-account-ebs",
            status="connected",
            role_arn="arn:aws:iam::123456789012:role/fptnext-validator",
            region_default="us-east-1",
        )
        db.add(cloud_account)
        db.commit()
        db.refresh(cloud_account)

        mock_fetch.return_value = [
            {
                "resource_id": "vol-0123456789abcdef0",
                "resource_type": "ebs_volume",
                "region": "us-east-1",
                "configuration_json": {"size_gb": 100, "state": "available", "attachments": []},
                "tags_json": {"Name": "data-volume"},
            }
        ]

        result = ingestion_service.ingest_ebs(db, tenant.id, cloud_account.id)

        assert result.cloud_account_id == cloud_account.id
        assert result.resource_type == "ebs_volume"
        assert result.ingested_count == 1
        assert result.captured_at is not None


class TestIngestionEndpoint:
    """Test EC2 ingestion API endpoint."""

    @patch("app.services.aws_inventory_service.fetch_ec2_instances")
    def test_ingest_ec2_endpoint_success(self, mock_fetch, client, db: Session, dev_org_scope):
        """Test successful ingest endpoint."""
        org = dev_org_scope["org"]
        h = dev_org_scope["headers"]
        tenant = Tenant(name="test-tenant5", status="active", organization_id=org.id)
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        cloud_account = CloudAccount(
            tenant_id=tenant.id,
            account_id="123456789012",
            name="test-account",
            status="connected",
            role_arn="arn:aws:iam::123456789012:role/fptnext-validator",
            region_default="us-east-1",
        )
        db.add(cloud_account)
        db.commit()
        db.refresh(cloud_account)

        mock_fetch.return_value = [
            {
                "resource_id": "i-0123456789abcdef0",
                "resource_type": "ec2_instance",
                "region": "us-east-1",
                "configuration_json": {"instance_type": "t3.micro"},
                "tags_json": {"Name": "web-server"},
            }
        ]

        response = client.post(
            f"/api/v1/tenants/{tenant.id}/cloud-accounts/{cloud_account.id}/ingest/ec2",
            headers=h,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["cloud_account_id"] == str(cloud_account.id)
        assert data["resource_type"] == "ec2_instance"
        assert data["ingested_count"] == 1
        assert data["captured_at"] is not None

    def test_ingest_ec2_endpoint_tenant_not_found(self, client, dev_org_scope):
        """Test ingest endpoint with missing tenant."""
        fake_tenant_id = uuid4()
        fake_account_id = uuid4()

        response = client.post(
            f"/api/v1/tenants/{fake_tenant_id}/cloud-accounts/{fake_account_id}/ingest/ec2",
            headers=dev_org_scope["headers"],
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "Tenant not found" in response.json()["detail"]

    def test_ingest_ec2_endpoint_account_not_found(self, client, db: Session, dev_org_scope):
        """Test ingest endpoint with missing cloud account."""
        org = dev_org_scope["org"]
        h = dev_org_scope["headers"]
        tenant = Tenant(name="test-tenant6", status="active", organization_id=org.id)
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        fake_account_id = uuid4()

        response = client.post(
            f"/api/v1/tenants/{tenant.id}/cloud-accounts/{fake_account_id}/ingest/ec2",
            headers=h,
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "Cloud account not found" in response.json()["detail"]

    @patch("app.services.aws_inventory_service.fetch_rds_instances")
    @patch("app.services.aws_inventory_service.fetch_aurora_clusters")
    def test_ingest_rds_endpoint_success(
        self, mock_fetch_clusters, mock_fetch_instances, client, db: Session, dev_org_scope
    ):
        """Test successful RDS ingest endpoint."""
        org = dev_org_scope["org"]
        h = dev_org_scope["headers"]
        tenant = Tenant(name="test-tenant-rds-endpoint", status="active", organization_id=org.id)
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        cloud_account = CloudAccount(
            tenant_id=tenant.id,
            account_id="123456789012",
            name="test-account-rds-endpoint",
            status="connected",
            role_arn="arn:aws:iam::123456789012:role/fptnext-validator",
            region_default="us-east-1",
        )
        db.add(cloud_account)
        db.commit()
        db.refresh(cloud_account)

        mock_fetch_instances.return_value = [
            {
                "resource_id": "app-db-1",
                "resource_type": "rds_instance",
                "region": "us-east-1",
                "configuration_json": {"engine": "postgres"},
                "tags_json": {"Name": "primary-db"},
            }
        ]
        mock_fetch_clusters.return_value = [
            {
                "resource_id": "aurora-main",
                "resource_type": "aurora_cluster",
                "region": "us-east-1",
                "configuration_json": {"engine": "aurora-postgresql"},
                "tags_json": {"Env": "prod"},
            }
        ]

        response = client.post(
            f"/api/v1/tenants/{tenant.id}/cloud-accounts/{cloud_account.id}/ingest/rds",
            headers=h,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["cloud_account_id"] == str(cloud_account.id)
        assert data["resource_type"] == "rds"
        assert data["ingested_count"] == 2
        assert data["captured_at"] is not None

    def test_ingest_rds_endpoint_tenant_not_found(self, client, dev_org_scope):
        """Test RDS ingest endpoint with missing tenant."""
        fake_tenant_id = uuid4()
        fake_account_id = uuid4()

        response = client.post(
            f"/api/v1/tenants/{fake_tenant_id}/cloud-accounts/{fake_account_id}/ingest/rds",
            headers=dev_org_scope["headers"],
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "Tenant not found" in response.json()["detail"]

    def test_ingest_rds_endpoint_account_not_found(self, client, db: Session, dev_org_scope):
        """Test RDS ingest endpoint with missing cloud account."""
        org = dev_org_scope["org"]
        h = dev_org_scope["headers"]
        tenant = Tenant(name="test-tenant-rds-not-found", status="active", organization_id=org.id)
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        fake_account_id = uuid4()

        response = client.post(
            f"/api/v1/tenants/{tenant.id}/cloud-accounts/{fake_account_id}/ingest/rds",
            headers=h,
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "Cloud account not found" in response.json()["detail"]

    @patch("app.services.aws_inventory_service.fetch_ebs_volumes")
    def test_ingest_ebs_endpoint_success(self, mock_fetch, client, db: Session, dev_org_scope):
        """Test successful EBS ingest endpoint."""
        org = dev_org_scope["org"]
        h = dev_org_scope["headers"]
        tenant = Tenant(name="test-tenant-ebs-endpoint", status="active", organization_id=org.id)
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        cloud_account = CloudAccount(
            tenant_id=tenant.id,
            account_id="123456789012",
            name="test-account-ebs-endpoint",
            status="connected",
            role_arn="arn:aws:iam::123456789012:role/fptnext-validator",
            region_default="us-east-1",
        )
        db.add(cloud_account)
        db.commit()
        db.refresh(cloud_account)

        mock_fetch.return_value = [
            {
                "resource_id": "vol-0123456789abcdef0",
                "resource_type": "ebs_volume",
                "region": "us-east-1",
                "configuration_json": {"size_gb": 100, "state": "available", "attachments": []},
                "tags_json": {"Name": "data-volume"},
            }
        ]

        response = client.post(
            f"/api/v1/tenants/{tenant.id}/cloud-accounts/{cloud_account.id}/ingest/ebs",
            headers=h,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["cloud_account_id"] == str(cloud_account.id)
        assert data["resource_type"] == "ebs_volume"
        assert data["ingested_count"] == 1
