"""Tests for the AWS CloudFormation self-service onboarding flow.

Covers:
  - start_onboarding: creates a CloudAccount skeleton + sets up statuses
  - External ID + token are auto-generated (unique, non-empty)
  - build_cfn_launch_params / to_read
  - confirm_roles: ARN validation, status advance
  - validate_onboarding: success path (mocked STS), failure path
  - API endpoints: /start, /by-token/{token}, confirm-roles, validate
  - Sync kickoff is attempted on successful validation
  - Token-based (public) API endpoints
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.core.config import MEEZI_PLATFORM_AWS_ACCOUNT_ID
from app.services import aws_onboarding_service
from app.services.aws_validation_service import AwsValidationResult


# ── Helpers ────────────────────────────────────────────────────────────────────


def _create_tenant(client, headers: dict, name: str = "onboard-tenant") -> str:
    r = client.post("/api/v1/tenants", json={"name": name}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _start_via_api(client, headers: dict, tenant_id: str, **kwargs) -> dict:
    body = {
        "tenant_id": tenant_id,
        "name": "Test AWS Account",
        "region_default": "us-east-1",
        "onboarding_mode": "read_and_execution",
        **kwargs,
    }
    r = client.post("/api/v1/onboarding/start", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


# ── Unit: aws_onboarding_service ──────────────────────────────────────────────


class TestStartOnboarding:
    def test_creates_cloud_account_with_correct_fields(self, db, dev_org_scope):
        from app.models.tenant import Tenant
        from app.schemas.aws_onboarding import OnboardingStartRequest

        org = dev_org_scope["org"]
        tenant = Tenant(name="t1", organization_id=org.id, status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        req = OnboardingStartRequest(
            tenant_id=tenant.id,
            name="My AWS Prod",
            region_default="us-west-2",
            onboarding_mode="read_and_execution",
        )
        record = aws_onboarding_service.start_onboarding(db, tenant_id=tenant.id, data=req)

        assert record.id is not None
        assert record.tenant_id == tenant.id
        assert record.name == "My AWS Prod"
        assert record.region_default == "us-west-2"
        assert record.onboarding_mode == "read_and_execution"
        assert record.read_only_status == "pending"
        assert record.execution_status == "awaiting_role_arn"
        assert record.connection_status == "untested"

    def test_external_id_is_non_empty_and_secure(self, db, dev_org_scope):
        from app.models.tenant import Tenant
        from app.schemas.aws_onboarding import OnboardingStartRequest

        org = dev_org_scope["org"]
        tenant = Tenant(name="t2", organization_id=org.id, status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        req = OnboardingStartRequest(tenant_id=tenant.id, name="Ext ID test")
        record = aws_onboarding_service.start_onboarding(db, tenant_id=tenant.id, data=req)

        assert record.external_id
        assert len(record.external_id) >= 30

    def test_onboarding_tokens_are_unique(self, db, dev_org_scope):
        from app.models.tenant import Tenant
        from app.schemas.aws_onboarding import OnboardingStartRequest

        org = dev_org_scope["org"]
        tenant = Tenant(name="t3", organization_id=org.id, status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        req1 = OnboardingStartRequest(tenant_id=tenant.id, name="Acct A")
        req2 = OnboardingStartRequest(tenant_id=tenant.id, name="Acct B")
        r1 = aws_onboarding_service.start_onboarding(db, tenant_id=tenant.id, data=req1)
        r2 = aws_onboarding_service.start_onboarding(db, tenant_id=tenant.id, data=req2)
        assert r1.onboarding_token != r2.onboarding_token

    def test_external_ids_are_unique(self, db, dev_org_scope):
        from app.models.tenant import Tenant
        from app.schemas.aws_onboarding import OnboardingStartRequest

        org = dev_org_scope["org"]
        tenant = Tenant(name="t-extid", organization_id=org.id, status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        req1 = OnboardingStartRequest(tenant_id=tenant.id, name="X1")
        req2 = OnboardingStartRequest(tenant_id=tenant.id, name="X2")
        r1 = aws_onboarding_service.start_onboarding(db, tenant_id=tenant.id, data=req1)
        r2 = aws_onboarding_service.start_onboarding(db, tenant_id=tenant.id, data=req2)
        assert r1.external_id != r2.external_id

    def test_read_only_mode_disables_execution_role(self, db, dev_org_scope):
        from app.models.tenant import Tenant
        from app.schemas.aws_onboarding import OnboardingStartRequest

        org = dev_org_scope["org"]
        tenant = Tenant(name="t-ro", organization_id=org.id, status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        req = OnboardingStartRequest(
            tenant_id=tenant.id, name="Read-only account", onboarding_mode="read_only"
        )
        record = aws_onboarding_service.start_onboarding(db, tenant_id=tenant.id, data=req)
        assert record.execution_status == "not_configured"

    def test_raises_on_unknown_tenant(self, db, dev_org_scope):
        from app.schemas.aws_onboarding import OnboardingStartRequest

        req = OnboardingStartRequest(tenant_id=uuid4(), name="Ghost account")
        with pytest.raises(Exception):
            aws_onboarding_service.start_onboarding(db, tenant_id=req.tenant_id, data=req)


class TestBuildCfnLaunchParams:
    def test_cfn_launch_url_contains_external_id(self, db, dev_org_scope):
        from app.models.tenant import Tenant
        from app.schemas.aws_onboarding import OnboardingStartRequest

        org = dev_org_scope["org"]
        tenant = Tenant(name="t-cfn", organization_id=org.id, status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        req = OnboardingStartRequest(tenant_id=tenant.id, name="CFN Acct")
        record = aws_onboarding_service.start_onboarding(db, tenant_id=tenant.id, data=req)
        params = aws_onboarding_service.build_cfn_launch_params(record)

        assert record.external_id in params.cfn_launch_url
        assert "cloudformation" in params.cfn_launch_url
        assert "FptNextRoles" in params.cfn_launch_url

    def test_cfn_include_execution_role_flag(self, db, dev_org_scope):
        from app.models.tenant import Tenant
        from app.schemas.aws_onboarding import OnboardingStartRequest

        org = dev_org_scope["org"]
        tenant = Tenant(name="t-cfn2", organization_id=org.id, status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        req_r = OnboardingStartRequest(tenant_id=tenant.id, name="RO", onboarding_mode="read_only")
        req_e = OnboardingStartRequest(tenant_id=tenant.id, name="RE", onboarding_mode="read_and_execution")

        r_ro = aws_onboarding_service.start_onboarding(db, tenant_id=tenant.id, data=req_r)
        r_re = aws_onboarding_service.start_onboarding(db, tenant_id=tenant.id, data=req_e)

        params_ro = aws_onboarding_service.build_cfn_launch_params(r_ro)
        params_re = aws_onboarding_service.build_cfn_launch_params(r_re)

        assert params_ro.include_execution_role is False
        assert params_re.include_execution_role is True

    def test_platform_account_id_is_meezi_constant(self, db, dev_org_scope):
        """build_cfn_launch_params always returns MEEZI_PLATFORM_AWS_ACCOUNT_ID."""
        from app.models.tenant import Tenant
        from app.schemas.aws_onboarding import OnboardingStartRequest

        org = dev_org_scope["org"]
        tenant = Tenant(name="t-plat", organization_id=org.id, status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        req = OnboardingStartRequest(tenant_id=tenant.id, name="Platform ID test")
        record = aws_onboarding_service.start_onboarding(db, tenant_id=tenant.id, data=req)
        params = aws_onboarding_service.build_cfn_launch_params(record)

        assert params.platform_aws_account_id == MEEZI_PLATFORM_AWS_ACCOUNT_ID

    def test_platform_account_id_never_placeholder(self, db, dev_org_scope):
        """The old 'YOUR_PLATFORM_AWS_ACCOUNT_ID' placeholder must never appear."""
        from app.models.tenant import Tenant
        from app.schemas.aws_onboarding import OnboardingStartRequest

        org = dev_org_scope["org"]
        tenant = Tenant(name="t-noplaceholder", organization_id=org.id, status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        req = OnboardingStartRequest(tenant_id=tenant.id, name="No placeholder test")
        record = aws_onboarding_service.start_onboarding(db, tenant_id=tenant.id, data=req)
        params = aws_onboarding_service.build_cfn_launch_params(record)

        assert "YOUR_PLATFORM" not in params.platform_aws_account_id
        assert params.platform_aws_account_id.isdigit()
        assert len(params.platform_aws_account_id) == 12

    def test_cfn_launch_url_contains_meezi_account_id(self, db, dev_org_scope):
        """The CloudFormation launch URL must contain the MEEZI platform account ID."""
        from app.models.tenant import Tenant
        from app.schemas.aws_onboarding import OnboardingStartRequest

        org = dev_org_scope["org"]
        tenant = Tenant(name="t-urlacct", organization_id=org.id, status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        req = OnboardingStartRequest(tenant_id=tenant.id, name="URL account test")
        record = aws_onboarding_service.start_onboarding(db, tenant_id=tenant.id, data=req)
        params = aws_onboarding_service.build_cfn_launch_params(record)

        assert MEEZI_PLATFORM_AWS_ACCOUNT_ID in params.cfn_launch_url

    def test_meezi_constant_has_correct_value(self):
        """The MEEZI_PLATFORM_AWS_ACCOUNT_ID constant is the expected account."""
        assert MEEZI_PLATFORM_AWS_ACCOUNT_ID == "135053815591"
        assert len(MEEZI_PLATFORM_AWS_ACCOUNT_ID) == 12
        assert MEEZI_PLATFORM_AWS_ACCOUNT_ID.isdigit()

    def test_env_override_respected(self, db, dev_org_scope):
        """When FPTNEXT_PLATFORM_AWS_ACCOUNT_ID env var is set, it overrides the default."""
        from app.models.tenant import Tenant
        from app.schemas.aws_onboarding import OnboardingStartRequest

        org = dev_org_scope["org"]
        tenant = Tenant(name="t-envoverride", organization_id=org.id, status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        req = OnboardingStartRequest(tenant_id=tenant.id, name="Env override test")
        record = aws_onboarding_service.start_onboarding(db, tenant_id=tenant.id, data=req)

        custom_id = "999999999999"
        with patch("app.services.aws_onboarding_service.settings") as mock_settings:
            mock_settings.platform_aws_account_id = custom_id
            mock_settings.api_public_url = "http://test.local"
            params = aws_onboarding_service.build_cfn_launch_params(record)
            assert params.platform_aws_account_id == custom_id

    def test_template_url_is_s3_by_default(self, db, dev_org_scope):
        """The template URL must be an S3 URL so CloudFormation quick-create works."""
        from app.models.tenant import Tenant
        from app.schemas.aws_onboarding import OnboardingStartRequest

        org = dev_org_scope["org"]
        tenant = Tenant(name="t-s3default", organization_id=org.id, status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        req = OnboardingStartRequest(tenant_id=tenant.id, name="S3 default test")
        record = aws_onboarding_service.start_onboarding(db, tenant_id=tenant.id, data=req)
        params = aws_onboarding_service.build_cfn_launch_params(record)

        assert "amazonaws.com" in params.template_url
        assert "s3" in params.template_url
        assert params.template_url.endswith("fptnext-roles.yaml")

    def test_template_url_is_not_localhost(self, db, dev_org_scope):
        """The template URL must never default to localhost (unreachable by AWS)."""
        from app.models.tenant import Tenant
        from app.schemas.aws_onboarding import OnboardingStartRequest

        org = dev_org_scope["org"]
        tenant = Tenant(name="t-nolocalhost", organization_id=org.id, status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        req = OnboardingStartRequest(tenant_id=tenant.id, name="No localhost test")
        record = aws_onboarding_service.start_onboarding(db, tenant_id=tenant.id, data=req)
        params = aws_onboarding_service.build_cfn_launch_params(record)

        assert "localhost" not in params.template_url
        assert "127.0.0.1" not in params.template_url

    def test_cfn_launch_url_templateURL_is_s3(self, db, dev_org_scope):
        """The templateURL query parameter in cfn_launch_url must be an S3 URL."""
        from app.models.tenant import Tenant
        from app.schemas.aws_onboarding import OnboardingStartRequest
        import urllib.parse

        org = dev_org_scope["org"]
        tenant = Tenant(name="t-s3launch", organization_id=org.id, status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        req = OnboardingStartRequest(tenant_id=tenant.id, name="S3 launch test")
        record = aws_onboarding_service.start_onboarding(db, tenant_id=tenant.id, data=req)
        params = aws_onboarding_service.build_cfn_launch_params(record)

        # The quick-create URL encodes params after the fragment path using ?
        # e.g. ...#/stacks/create/review?templateURL=...&stackName=...
        fragment = params.cfn_launch_url.split("#", 1)[-1]
        # Split on the first ? to get the query string portion of the fragment
        qs = fragment.split("?", 1)[-1] if "?" in fragment else ""
        qs_params = dict(urllib.parse.parse_qsl(qs))
        template_url_param = qs_params.get("templateURL", "")

        assert "amazonaws.com" in template_url_param
        assert "s3" in template_url_param

    def test_cf_template_base_url_override(self, db, dev_org_scope):
        """FPTNEXT_CF_TEMPLATE_BASE_URL env var overrides the default S3 base."""
        from app.models.tenant import Tenant
        from app.schemas.aws_onboarding import OnboardingStartRequest

        org = dev_org_scope["org"]
        tenant = Tenant(name="t-cfnoverride", organization_id=org.id, status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        req = OnboardingStartRequest(tenant_id=tenant.id, name="CFN override test")
        record = aws_onboarding_service.start_onboarding(db, tenant_id=tenant.id, data=req)

        custom_base = "https://custom-cdn.example.com/templates"
        with patch("app.services.aws_onboarding_service.settings") as mock_settings:
            mock_settings.cf_template_base_url = custom_base
            mock_settings.platform_aws_account_id = MEEZI_PLATFORM_AWS_ACCOUNT_ID
            params = aws_onboarding_service.build_cfn_launch_params(record)

        assert params.template_url == f"{custom_base}/fptnext-roles.yaml"


class TestConfirmRoles:
    def _make_record(self, db, org):
        from app.models.tenant import Tenant
        from app.schemas.aws_onboarding import OnboardingStartRequest

        tenant = Tenant(name=f"t-confirm-{uuid4().hex[:6]}", organization_id=org.id, status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        req = OnboardingStartRequest(tenant_id=tenant.id, name="Confirm Test")
        return aws_onboarding_service.start_onboarding(db, tenant_id=tenant.id, data=req)

    def test_confirm_stores_read_only_arn(self, db, dev_org_scope):
        record = self._make_record(db, dev_org_scope["org"])
        read_only_arn = "arn:aws:iam::123456789012:role/FptNextReadOnlyRole"
        updated = aws_onboarding_service.confirm_roles(
            db, record, read_only_role_arn=read_only_arn, execution_role_arn=None
        )
        assert updated.role_arn == read_only_arn
        assert updated.read_only_status == "validating"

    def test_confirm_derives_account_id_from_arn(self, db, dev_org_scope):
        record = self._make_record(db, dev_org_scope["org"])
        updated = aws_onboarding_service.confirm_roles(
            db,
            record,
            read_only_role_arn="arn:aws:iam::999888777666:role/FptNextReadOnlyRole",
            execution_role_arn=None,
        )
        assert updated.account_id == "999888777666"

    def test_confirm_stores_execution_arn(self, db, dev_org_scope):
        record = self._make_record(db, dev_org_scope["org"])
        exec_arn = "arn:aws:iam::123456789012:role/FptNextExecutionRole"
        updated = aws_onboarding_service.confirm_roles(
            db,
            record,
            read_only_role_arn="arn:aws:iam::123456789012:role/FptNextReadOnlyRole",
            execution_role_arn=exec_arn,
        )
        assert updated.execution_role_arn == exec_arn
        assert updated.execution_status == "validating"


class TestValidateOnboarding:
    def _make_confirmed_record(self, db, org):
        from app.models.tenant import Tenant
        from app.schemas.aws_onboarding import OnboardingStartRequest

        tenant = Tenant(name=f"t-val-{uuid4().hex[:6]}", organization_id=org.id, status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        req = OnboardingStartRequest(tenant_id=tenant.id, name="Val Test")
        record = aws_onboarding_service.start_onboarding(db, tenant_id=tenant.id, data=req)
        return aws_onboarding_service.confirm_roles(
            db,
            record,
            read_only_role_arn="arn:aws:iam::123456789012:role/FptNextReadOnlyRole",
            execution_role_arn=None,
        )

    def test_success_marks_connected(self, db, dev_org_scope):
        record = self._make_confirmed_record(db, dev_org_scope["org"])
        with patch(
            "app.services.aws_onboarding_service.aws_validation_service.validate_cloud_account_role",
            return_value=AwsValidationResult(success=True, aws_account_id="123456789012"),
        ):
            result = aws_onboarding_service.validate_onboarding(db, record)

        assert result.read_only_validated is True
        assert result.aws_account_id == "123456789012"
        assert record.read_only_status == "connected"
        assert record.connection_status == "valid"

    def test_failure_marks_failed(self, db, dev_org_scope):
        record = self._make_confirmed_record(db, dev_org_scope["org"])
        with patch(
            "app.services.aws_onboarding_service.aws_validation_service.validate_cloud_account_role",
            return_value=AwsValidationResult(
                success=False, error_message="AccessDenied: Not authorized to assume role"
            ),
        ):
            result = aws_onboarding_service.validate_onboarding(db, record)

        assert result.read_only_validated is False
        assert result.read_only_error is not None
        assert record.read_only_status == "failed"
        assert record.connection_status == "invalid"

    def test_validates_execution_role_when_present(self, db, dev_org_scope):
        from app.models.tenant import Tenant
        from app.schemas.aws_onboarding import OnboardingStartRequest

        org = dev_org_scope["org"]
        tenant = Tenant(name=f"t-exec-{uuid4().hex[:6]}", organization_id=org.id, status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        req = OnboardingStartRequest(tenant_id=tenant.id, name="Exec Val", onboarding_mode="read_and_execution")
        record = aws_onboarding_service.start_onboarding(db, tenant_id=tenant.id, data=req)
        record = aws_onboarding_service.confirm_roles(
            db,
            record,
            read_only_role_arn="arn:aws:iam::123456789012:role/FptNextReadOnlyRole",
            execution_role_arn="arn:aws:iam::123456789012:role/FptNextExecutionRole",
        )

        with patch(
            "app.services.aws_onboarding_service.aws_validation_service.validate_cloud_account_role",
            return_value=AwsValidationResult(success=True, aws_account_id="123456789012"),
        ):
            result = aws_onboarding_service.validate_onboarding(db, record)

        assert result.read_only_validated is True
        assert result.execution_validated is True
        assert record.execution_status == "connected"


# ── API endpoint tests ─────────────────────────────────────────────────────────


class TestOnboardingStartEndpoint:
    def test_start_returns_201_with_cfn_launch(self, client, dev_org_scope):
        h = dev_org_scope["headers"]
        tid = _create_tenant(client, h)
        data = _start_via_api(client, h, tid)

        assert data["id"]
        assert data["external_id"]
        assert data["onboarding_mode"] == "read_and_execution"
        assert data["read_only_status"] == "pending"
        assert data["cfn_launch"]["cfn_launch_url"]
        assert "cloudformation" in data["cfn_launch"]["cfn_launch_url"]

    def test_start_stores_onboarding_token(self, client, dev_org_scope):
        h = dev_org_scope["headers"]
        tid = _create_tenant(client, h, name="t-token-store")
        data = _start_via_api(client, h, tid)
        assert data["onboarding_token"]

    def test_start_unknown_tenant_returns_404(self, client, dev_org_scope):
        h = dev_org_scope["headers"]
        r = client.post(
            "/api/v1/onboarding/start",
            json={
                "tenant_id": str(uuid4()),
                "name": "Ghost",
                "region_default": "us-east-1",
                "onboarding_mode": "read_only",
            },
            headers=h,
        )
        assert r.status_code == 404

    def test_start_requires_auth(self, client):
        r = client.post(
            "/api/v1/onboarding/start",
            json={
                "tenant_id": str(uuid4()),
                "name": "Ghost",
                "region_default": "us-east-1",
                "onboarding_mode": "read_only",
            },
        )
        # 401 or 403 — not 201
        assert r.status_code in (401, 403, 422)


class TestOnboardingByTokenEndpoint:
    def test_by_token_returns_session(self, client, dev_org_scope):
        h = dev_org_scope["headers"]
        tid = _create_tenant(client, h, name="t-for-token")
        session = _start_via_api(client, h, tid)
        token = session["onboarding_token"]

        r = client.get(f"/api/v1/onboarding/by-token/{token}")
        assert r.status_code == 200
        assert r.json()["id"] == session["id"]

    def test_invalid_token_returns_404(self, client):
        r = client.get("/api/v1/onboarding/by-token/invalid-token-xyz")
        assert r.status_code == 404


class TestConfirmRolesEndpoint:
    def _make_session(self, client, dev_org_scope):
        h = dev_org_scope["headers"]
        tid = _create_tenant(client, h, name=f"t-cr-{uuid4().hex[:6]}")
        return _start_via_api(client, h, tid), h

    def test_confirm_roles_advances_status(self, client, dev_org_scope):
        session, h = self._make_session(client, dev_org_scope)
        sid = session["id"]

        r = client.patch(
            f"/api/v1/onboarding/{sid}/confirm-roles",
            json={"read_only_role_arn": "arn:aws:iam::111222333444:role/FptNextReadOnlyRole"},
            headers=h,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["read_only_status"] == "validating"
        assert data["role_arn"] == "arn:aws:iam::111222333444:role/FptNextReadOnlyRole"

    def test_confirm_roles_bad_arn_returns_422(self, client, dev_org_scope):
        session, h = self._make_session(client, dev_org_scope)
        r = client.patch(
            f"/api/v1/onboarding/{session['id']}/confirm-roles",
            json={"read_only_role_arn": "not-a-valid-arn"},
            headers=h,
        )
        assert r.status_code == 422


class TestValidateEndpoint:
    def _confirmed_session(self, client, dev_org_scope):
        h = dev_org_scope["headers"]
        tid = _create_tenant(client, h, name=f"t-val-{uuid4().hex[:6]}")
        session = _start_via_api(client, h, tid)
        sid = session["id"]
        client.patch(
            f"/api/v1/onboarding/{sid}/confirm-roles",
            json={"read_only_role_arn": "arn:aws:iam::111222333444:role/FptNextReadOnlyRole"},
            headers=h,
        )
        return sid, h

    def test_validate_success_queues_sync(self, client, dev_org_scope):
        sid, h = self._confirmed_session(client, dev_org_scope)

        mock_job = MagicMock()
        mock_job.id = uuid4()

        with patch(
            "app.api.v1.aws_onboarding.aws_onboarding_service.validate_onboarding",
            return_value=AwsValidationResult(success=True, aws_account_id="111222333444"),
        ) as mock_validate, patch(
            "app.api.v1.aws_onboarding.ingestion_job_service.create_sync_job",
            return_value=mock_job,
        ) as mock_sync:
            # Patch OnboardingValidationResult to match what the mocked function returns
            from app.schemas.aws_onboarding import OnboardingValidationResult
            mock_validate.return_value = OnboardingValidationResult(
                read_only_validated=True,
                execution_validated=None,
                aws_account_id="111222333444",
            )
            r = client.post(f"/api/v1/onboarding/{sid}/validate", headers=h)

        assert r.status_code == 200
        assert mock_sync.called

    def test_validate_without_arn_returns_422(self, client, dev_org_scope):
        h = dev_org_scope["headers"]
        tid = _create_tenant(client, h, name=f"t-noarn-{uuid4().hex[:6]}")
        session = _start_via_api(client, h, tid)  # not yet confirmed
        r = client.post(f"/api/v1/onboarding/{session['id']}/validate", headers=h)
        assert r.status_code == 422

    def test_validate_missing_session_returns_404(self, client, dev_org_scope):
        h = dev_org_scope["headers"]
        r = client.post(f"/api/v1/onboarding/{uuid4()}/validate", headers=h)
        assert r.status_code == 404


class TestCfnTemplateEndpoint:
    def test_cfn_template_returns_yaml(self, client):
        r = client.get("/api/v1/onboarding/cfn-template")
        # 200 if template file exists, 503 if running in test env without file
        assert r.status_code in (200, 503)
        if r.status_code == 200:
            assert "AWSTemplateFormatVersion" in r.text or "Parameters" in r.text


class TestTokenBasedConfirmEndpoint:
    def test_token_confirm_roles(self, client, dev_org_scope):
        h = dev_org_scope["headers"]
        tid = _create_tenant(client, h, name=f"t-tok-cr-{uuid4().hex[:6]}")
        session = _start_via_api(client, h, tid)
        token = session["onboarding_token"]

        r = client.patch(
            f"/api/v1/onboarding/by-token/{token}/confirm-roles",
            json={"read_only_role_arn": "arn:aws:iam::222333444555:role/FptNextReadOnlyRole"},
        )
        assert r.status_code == 200
        assert r.json()["read_only_status"] == "validating"

    def test_token_confirm_invalid_token_returns_404(self, client):
        r = client.patch(
            "/api/v1/onboarding/by-token/bogus-token/confirm-roles",
            json={"read_only_role_arn": "arn:aws:iam::222333444555:role/FptNextReadOnlyRole"},
        )
        assert r.status_code == 404
