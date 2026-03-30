"""Tests for AWS validation service and cloud account validation endpoint."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import status
from sqlalchemy.orm import Session

from app.api.v1.cloud_accounts import router as cloud_accounts_router
from app.models.cloud_account import CloudAccount
from app.models.tenant import Tenant
from app.services import aws_validation_service
from app.services.cloud_account_service import validate_cloud_account


class TestAwsValidationService:
    """Test AWS validation service directly."""

    def test_validation_result_success(self):
        """Test AwsValidationResult with success."""
        result = aws_validation_service.AwsValidationResult(
            success=True,
            aws_account_id="123456789012",
            arn="arn:aws:iam::123456789012:user/test",
            user_id="AIDACKCEVSQ6C2EXAMPLE",
        )

        assert result.success is True
        assert result.aws_account_id == "123456789012"
        assert result.arn == "arn:aws:iam::123456789012:user/test"
        assert result.user_id == "AIDACKCEVSQ6C2EXAMPLE"
        assert result.error_message is None

    def test_validation_result_failure(self):
        """Test AwsValidationResult with failure."""
        result = aws_validation_service.AwsValidationResult(
            success=False,
            error_message="AccessDenied: User is not authorized to perform: sts:AssumeRole",
        )

        assert result.success is False
        assert result.aws_account_id is None
        assert result.error_message == "AccessDenied: User is not authorized to perform: sts:AssumeRole"

    @patch("app.services.aws_validation_service.boto3.client")
    def test_validate_cloud_account_role_success(self, mock_boto3_client):
        """Test successful AWS role validation."""
        # Mock STS client calls
        mock_sts_client = MagicMock()
        mock_boto3_client.return_value = mock_sts_client

        # Mock assume_role response
        mock_sts_client.assume_role.return_value = {
            "Credentials": {
                "AccessKeyId": "ASIAIOSFODNN7EXAMPLE",
                "SecretAccessKey": "example_secret",
                "SessionToken": "example_token",
            }
        }

        # Mock get_caller_identity response
        mock_sts_client.get_caller_identity.return_value = {
            "Account": "123456789012",
            "Arn": "arn:aws:iam::123456789012:user/test",
            "UserId": "AIDACKCEVSQ6C2EXAMPLE",
        }

        role_arn = "arn:aws:iam::123456789012:role/fptnext-validator"
        result = aws_validation_service.validate_cloud_account_role(role_arn, region="us-east-1")

        assert result.success is True
        assert result.aws_account_id == "123456789012"
        assert result.arn == "arn:aws:iam::123456789012:user/test"
        assert result.user_id == "AIDACKCEVSQ6C2EXAMPLE"
        assert result.error_message is None

        # Verify boto3 was called correctly
        assert mock_boto3_client.call_count >= 1
        mock_sts_client.assume_role.assert_called_once()

    @patch("app.services.aws_validation_service.boto3.client")
    def test_validate_cloud_account_role_access_denied(self, mock_boto3_client):
        """Test AWS role validation with access denied."""
        from botocore.exceptions import ClientError

        mock_sts_client = MagicMock()
        mock_boto3_client.return_value = mock_sts_client

        # Mock assume_role to raise AccessDenied
        error_response = {
            "Error": {
                "Code": "AccessDenied",
                "Message": "User is not authorized to perform: sts:AssumeRole",
            }
        }
        mock_sts_client.assume_role.side_effect = ClientError(error_response, "AssumeRole")

        role_arn = "arn:aws:iam::123456789012:role/fptnext-validator"
        result = aws_validation_service.validate_cloud_account_role(role_arn)

        assert result.success is False
        assert result.error_message.startswith("AccessDenied")
        assert result.aws_account_id is None


class TestCloudAccountValidation:
    """Test cloud account validation endpoint."""

    def test_validate_cloud_account_success(self, db: Session):
        """Test successful validation of cloud account."""
        # Create test data
        tenant = Tenant(name="test-tenant", status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        cloud_account = CloudAccount(
            tenant_id=tenant.id,
            account_id="123456789012",
            name="test-account",
            status="pending",
            role_arn="arn:aws:iam::123456789012:role/fptnext-validator",
            region_default="us-east-1",
        )
        db.add(cloud_account)
        db.commit()
        db.refresh(cloud_account)

        # Mock AWS validation
        with patch("app.services.aws_validation_service.validate_cloud_account_role") as mock_validate:
            mock_validate.return_value = aws_validation_service.AwsValidationResult(
                success=True,
                aws_account_id="123456789012",
                arn="arn:aws:iam::123456789012:user/test",
                user_id="AIDACKCEVSQ6C2EXAMPLE",
            )

            # Call validate_cloud_account
            updated_account, validation_result = validate_cloud_account(
                db,
                tenant.id,
                cloud_account.id,
            )

            # Verify results
            assert validation_result.success is True
            assert updated_account.status == "connected"
            assert updated_account.id == cloud_account.id

    def test_validate_cloud_account_failure(self, db: Session):
        """Test validation failure updates status to 'failed'."""
        # Create test data
        tenant = Tenant(name="test-tenant2", status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        cloud_account = CloudAccount(
            tenant_id=tenant.id,
            account_id="123456789012",
            name="test-account",
            status="pending",
            role_arn="arn:aws:iam::123456789012:role/fptnext-validator",
            region_default="us-east-1",
        )
        db.add(cloud_account)
        db.commit()
        db.refresh(cloud_account)

        # Mock AWS validation failure
        with patch("app.services.aws_validation_service.validate_cloud_account_role") as mock_validate:
            mock_validate.return_value = aws_validation_service.AwsValidationResult(
                success=False,
                error_message="AccessDenied: User is not authorized",
            )

            # Call validate_cloud_account
            updated_account, validation_result = validate_cloud_account(
                db,
                tenant.id,
                cloud_account.id,
            )

            # Verify results
            assert validation_result.success is False
            assert updated_account.status == "failed"
            assert "AccessDenied" in validation_result.error_message

    def test_validate_cloud_account_tenant_not_found(self, db: Session):
        """Test validation raises error when tenant not found."""
        fake_tenant_id = uuid4()
        fake_account_id = uuid4()

        with pytest.raises(ValueError, match="tenant_not_found"):
            validate_cloud_account(db, fake_tenant_id, fake_account_id)

    def test_validate_cloud_account_not_found(self, db: Session):
        """Test validation raises error when cloud account not found."""
        # Create tenant
        tenant = Tenant(name="test-tenant3", status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        fake_account_id = uuid4()

        with pytest.raises(ValueError, match="cloud_account_not_found"):
            validate_cloud_account(db, tenant.id, fake_account_id)

    def test_validate_endpoint_success(self, client, db: Session, dev_org_scope):
        """Test validate endpoint returns 200 on success."""
        org = dev_org_scope["org"]
        h = dev_org_scope["headers"]
        tenant = Tenant(name="test-tenant4", status="active", organization_id=org.id)
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        cloud_account = CloudAccount(
            tenant_id=tenant.id,
            account_id="123456789012",
            name="test-account",
            status="pending",
            role_arn="arn:aws:iam::123456789012:role/fptnext-validator",
            region_default="us-east-1",
        )
        db.add(cloud_account)
        db.commit()
        db.refresh(cloud_account)

        # Mock AWS validation
        with patch("app.services.aws_validation_service.validate_cloud_account_role") as mock_validate:
            mock_validate.return_value = aws_validation_service.AwsValidationResult(
                success=True,
                aws_account_id="123456789012",
                arn="arn:aws:iam::123456789012:user/test",
                user_id="AIDACKCEVSQ6C2EXAMPLE",
            )

            response = client.post(
                f"/api/v1/tenants/{tenant.id}/cloud-accounts/{cloud_account.id}/validate",
                headers=h,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["success"] is True
            assert data["status"] == "connected"
            assert data["aws_account_id"] == "123456789012"
            assert data["cloud_account_id"] == str(cloud_account.id)

    def test_validate_endpoint_aws_failure(self, client, db: Session, dev_org_scope):
        """Test validate endpoint returns 200 even when AWS validation fails."""
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
            status="pending",
            role_arn="arn:aws:iam::123456789012:role/fptnext-validator",
            region_default="us-east-1",
        )
        db.add(cloud_account)
        db.commit()
        db.refresh(cloud_account)

        # Mock AWS validation failure
        with patch("app.services.aws_validation_service.validate_cloud_account_role") as mock_validate:
            mock_validate.return_value = aws_validation_service.AwsValidationResult(
                success=False,
                error_message="AccessDenied: User is not authorized",
            )

            response = client.post(
                f"/api/v1/tenants/{tenant.id}/cloud-accounts/{cloud_account.id}/validate",
                headers=h,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["success"] is False
            assert data["status"] == "failed"
            assert "AccessDenied" in data["error_message"]

    def test_validate_endpoint_tenant_not_found(self, client, dev_org_scope):
        """Test validate endpoint returns 404 when tenant not found."""
        fake_tenant_id = uuid4()
        fake_account_id = uuid4()

        response = client.post(
            f"/api/v1/tenants/{fake_tenant_id}/cloud-accounts/{fake_account_id}/validate",
            headers=dev_org_scope["headers"],
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_validate_endpoint_cloud_account_not_found(self, client, db: Session, dev_org_scope):
        """Test validate endpoint returns 404 when cloud account not found."""
        org = dev_org_scope["org"]
        h = dev_org_scope["headers"]
        tenant = Tenant(name="test-tenant6", status="active", organization_id=org.id)
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        fake_account_id = uuid4()

        response = client.post(
            f"/api/v1/tenants/{tenant.id}/cloud-accounts/{fake_account_id}/validate",
            headers=h,
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
