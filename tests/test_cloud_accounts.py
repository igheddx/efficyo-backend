from unittest.mock import patch

from botocore.exceptions import ClientError


def _create_tenant(client, dev_org_scope, name: str = "tenant-for-cloud") -> str:
    h = dev_org_scope["headers"]
    response = client.post("/api/v1/tenants", json={"name": name}, headers=h)
    assert response.status_code == 201
    return response.json()["id"]


def test_create_cloud_account_as_msp_admin(client, dev_org_scope_admin):
    h = dev_org_scope_admin["headers"]
    tid = _create_tenant(client, dev_org_scope_admin, name="tenant-msp")
    response = client.post(
        f"/api/v1/tenants/{tid}/cloud-accounts",
        headers=h,
        json={
            "account_id": "123456789012",
            "name": "primary-account",
            "role_arn": "arn:aws:iam::123456789012:role/OptimizationRole",
            "region_default": "us-east-1",
        },
    )
    assert response.status_code == 201


def test_create_cloud_account_under_tenant(client, dev_org_scope):
    h = dev_org_scope["headers"]
    tenant_id = _create_tenant(client, dev_org_scope)

    response = client.post(
        f"/api/v1/tenants/{tenant_id}/cloud-accounts",
        headers=h,
        json={
            "account_id": "123456789012",
            "name": "primary-account",
            "role_arn": "arn:aws:iam::123456789012:role/OptimizationRole",
            "region_default": "us-east-1",
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["tenant_id"] == tenant_id
    assert data["account_id"] == "123456789012"
    assert data["status"] == "pending"
    assert data.get("connection_status") == "untested"
    assert data.get("initial_sync_job") is None


def test_list_cloud_accounts_for_tenant(client, dev_org_scope):
    h = dev_org_scope["headers"]
    tenant_id = _create_tenant(client, dev_org_scope)

    client.post(
        f"/api/v1/tenants/{tenant_id}/cloud-accounts",
        headers=h,
        json={
            "account_id": "123456789012",
            "name": "primary-account",
            "role_arn": "arn:aws:iam::123456789012:role/OptimizationRole",
            "region_default": "us-east-1",
        },
    )
    client.post(
        f"/api/v1/tenants/{tenant_id}/cloud-accounts",
        headers=h,
        json={
            "account_id": "210987654321",
            "name": "secondary-account",
            "role_arn": "arn:aws:iam::210987654321:role/OptimizationRole",
            "region_default": "us-west-2",
        },
    )

    response = client.get(f"/api/v1/tenants/{tenant_id}/cloud-accounts", headers=h)

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert {item["account_id"] for item in data} == {"123456789012", "210987654321"}


def test_get_one_cloud_account(client, dev_org_scope):
    h = dev_org_scope["headers"]
    tenant_id = _create_tenant(client, dev_org_scope)

    create_response = client.post(
        f"/api/v1/tenants/{tenant_id}/cloud-accounts",
        headers=h,
        json={
            "account_id": "123456789012",
            "name": "primary-account",
            "role_arn": "arn:aws:iam::123456789012:role/OptimizationRole",
            "region_default": "us-east-1",
        },
    )
    cloud_account_id = create_response.json()["id"]

    response = client.get(
        f"/api/v1/tenants/{tenant_id}/cloud-accounts/{cloud_account_id}", headers=h
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == cloud_account_id
    assert data["account_id"] == "123456789012"


def test_duplicate_account_id_same_tenant_returns_conflict(client, dev_org_scope):
    h = dev_org_scope["headers"]
    tenant_id = _create_tenant(client, dev_org_scope)
    payload = {
        "account_id": "123456789012",
        "name": "primary-account",
        "role_arn": "arn:aws:iam::123456789012:role/OptimizationRole",
        "region_default": "us-east-1",
    }

    first = client.post(f"/api/v1/tenants/{tenant_id}/cloud-accounts", json=payload, headers=h)
    second = client.post(f"/api/v1/tenants/{tenant_id}/cloud-accounts", json=payload, headers=h)

    assert first.status_code == 201
    assert second.status_code == 409


def test_get_cost_summary_success(client, dev_org_scope, monkeypatch):
    h = dev_org_scope["headers"]
    tenant_id = _create_tenant(client, dev_org_scope)
    create_response = client.post(
        f"/api/v1/tenants/{tenant_id}/cloud-accounts",
        headers=h,
        json={
            "account_id": "123456789012",
            "name": "primary-account",
            "role_arn": "arn:aws:iam::123456789012:role/OptimizationRole",
            "region_default": "us-east-1",
        },
    )
    cloud_account_id = create_response.json()["id"]

    monkeypatch.setattr(
        "app.services.cost_summary_service.get_cost_summary",
        lambda db_session, tenant_id, cloud_account_id: {
            "start_date": "2026-02-24",
            "end_date": "2026-03-25",
            "total_cost": 123.45,
            "by_service": [
                {"service": "Amazon Relational Database Service", "amount": 45.67},
                {"service": "AWS Lambda", "amount": 12.34},
            ],
        },
    )

    response = client.get(
        f"/api/v1/tenants/{tenant_id}/cloud-accounts/{cloud_account_id}/cost-summary", headers=h
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["start_date"] == "2026-02-24"
    assert payload["end_date"] == "2026-03-25"
    assert payload["total_cost"] == 123.45
    assert payload["by_service"][0]["service"] == "Amazon Relational Database Service"
    assert payload["cost_window"] == "rolling_30d"
    assert payload["cost_window_label"] == "Rolling last 30 days"
    assert payload["cost_metric"] == "UnblendedCost"


def test_get_cost_summary_tenant_not_found(client, dev_org_scope):
    response = client.get(
        "/api/v1/tenants/00000000-0000-0000-0000-000000000000/cloud-accounts/00000000-0000-0000-0000-000000000000/cost-summary",
        headers=dev_org_scope["headers"],
    )
    assert response.status_code == 404
    assert "Tenant not found" in response.json()["detail"]


def test_get_cost_summary_cloud_account_not_found(client, dev_org_scope):
    h = dev_org_scope["headers"]
    tenant_id = _create_tenant(client, dev_org_scope)

    response = client.get(
        f"/api/v1/tenants/{tenant_id}/cloud-accounts/00000000-0000-0000-0000-000000000000/cost-summary",
        headers=h,
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Cloud account not found"


def test_get_cost_summary_maps_aws_permission_error_to_502(client, dev_org_scope, monkeypatch):
    h = dev_org_scope["headers"]
    tenant_id = _create_tenant(client, dev_org_scope)
    create_response = client.post(
        f"/api/v1/tenants/{tenant_id}/cloud-accounts",
        headers=h,
        json={
            "account_id": "123456789012",
            "name": "primary-account",
            "role_arn": "arn:aws:iam::123456789012:role/OptimizationRole",
            "region_default": "us-east-1",
        },
    )
    cloud_account_id = create_response.json()["id"]

    client_error = ClientError(
        error_response={"Error": {"Code": "AccessDeniedException", "Message": "not authorized"}},
        operation_name="GetCostAndUsage",
    )

    def _raise_client_error(db_session, tenant_id, cloud_account_id):
        raise client_error

    monkeypatch.setattr("app.services.cost_summary_service.get_cost_summary", _raise_client_error)

    response = client.get(
        f"/api/v1/tenants/{tenant_id}/cloud-accounts/{cloud_account_id}/cost-summary", headers=h
    )

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["error_type"] == "cost_snapshot_unavailable"


def test_get_cost_summary_maps_aws_unavailable_error_to_502(client, dev_org_scope, monkeypatch):
    h = dev_org_scope["headers"]
    tenant_id = _create_tenant(client, dev_org_scope)
    create_response = client.post(
        f"/api/v1/tenants/{tenant_id}/cloud-accounts",
        headers=h,
        json={
            "account_id": "123456789012",
            "name": "primary-account",
            "role_arn": "arn:aws:iam::123456789012:role/OptimizationRole",
            "region_default": "us-east-1",
        },
    )
    cloud_account_id = create_response.json()["id"]

    client_error = ClientError(
        error_response={"Error": {"Code": "DataUnavailableException", "Message": "data unavailable"}},
        operation_name="GetCostAndUsage",
    )

    def _raise_client_error(db_session, tenant_id, cloud_account_id):
        raise client_error

    monkeypatch.setattr("app.services.cost_summary_service.get_cost_summary", _raise_client_error)

    response = client.get(
        f"/api/v1/tenants/{tenant_id}/cloud-accounts/{cloud_account_id}/cost-summary", headers=h
    )

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["error_type"] == "cost_snapshot_unavailable"


def test_get_ec2_other_breakdown_success(client, dev_org_scope, monkeypatch):
    h = dev_org_scope["headers"]
    tenant_id = _create_tenant(client, dev_org_scope)
    create_response = client.post(
        f"/api/v1/tenants/{tenant_id}/cloud-accounts",
        headers=h,
        json={
            "account_id": "123456789012",
            "name": "primary-account",
            "role_arn": "arn:aws:iam::123456789012:role/OptimizationRole",
            "region_default": "us-east-1",
        },
    )
    cloud_account_id = create_response.json()["id"]

    monkeypatch.setattr(
        "app.services.cost_summary_service.get_ec2_other_breakdown",
        lambda db_session, tenant_id, cloud_account_id: {
            "ec2_other_total": 14.25,
            "breakdown": [
                {"category": "NAT Gateway", "amount": 10.0},
                {"category": "Data Transfer", "amount": 4.25},
            ],
        },
    )

    response = client.get(
        f"/api/v1/tenants/{tenant_id}/cloud-accounts/{cloud_account_id}/cost-breakdown/ec2-other",
        headers=h,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ec2_other_total"] == 14.25
    assert payload["breakdown"][0]["category"] == "NAT Gateway"
    assert payload["cost_window"] == "rolling_30d"
    assert payload["cost_window_label"] == "Rolling last 30 days"
    assert payload["cost_metric"] == "UnblendedCost"


def test_get_ec2_other_breakdown_tenant_not_found(client, dev_org_scope):
    response = client.get(
        "/api/v1/tenants/00000000-0000-0000-0000-000000000000/cloud-accounts/00000000-0000-0000-0000-000000000000/cost-breakdown/ec2-other",
        headers=dev_org_scope["headers"],
    )
    assert response.status_code == 404
    assert "Tenant not found" in response.json()["detail"]


def test_get_ec2_other_breakdown_cloud_account_not_found(client, dev_org_scope):
    h = dev_org_scope["headers"]
    tenant_id = _create_tenant(client, dev_org_scope)

    response = client.get(
        f"/api/v1/tenants/{tenant_id}/cloud-accounts/00000000-0000-0000-0000-000000000000/cost-breakdown/ec2-other",
        headers=h,
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Cloud account not found"


def test_get_ec2_other_breakdown_maps_aws_permission_error_to_502(client, dev_org_scope, monkeypatch):
    h = dev_org_scope["headers"]
    tenant_id = _create_tenant(client, dev_org_scope)
    create_response = client.post(
        f"/api/v1/tenants/{tenant_id}/cloud-accounts",
        headers=h,
        json={
            "account_id": "123456789012",
            "name": "primary-account",
            "role_arn": "arn:aws:iam::123456789012:role/OptimizationRole",
            "region_default": "us-east-1",
        },
    )
    cloud_account_id = create_response.json()["id"]

    client_error = ClientError(
        error_response={"Error": {"Code": "AccessDeniedException", "Message": "not authorized"}},
        operation_name="GetCostAndUsage",
    )

    def _raise_client_error(db_session, tenant_id, cloud_account_id):
        raise client_error

    monkeypatch.setattr("app.services.cost_summary_service.get_ec2_other_breakdown", _raise_client_error)

    response = client.get(
        f"/api/v1/tenants/{tenant_id}/cloud-accounts/{cloud_account_id}/cost-breakdown/ec2-other",
        headers=h,
    )

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["error_type"] == "cost_snapshot_unavailable"


def test_get_ec2_other_breakdown_maps_aws_unavailable_error_to_502(client, dev_org_scope, monkeypatch):
    h = dev_org_scope["headers"]
    tenant_id = _create_tenant(client, dev_org_scope)
    create_response = client.post(
        f"/api/v1/tenants/{tenant_id}/cloud-accounts",
        headers=h,
        json={
            "account_id": "123456789012",
            "name": "primary-account",
            "role_arn": "arn:aws:iam::123456789012:role/OptimizationRole",
            "region_default": "us-east-1",
        },
    )
    cloud_account_id = create_response.json()["id"]

    client_error = ClientError(
        error_response={"Error": {"Code": "DataUnavailableException", "Message": "data unavailable"}},
        operation_name="GetCostAndUsage",
    )

    def _raise_client_error(db_session, tenant_id, cloud_account_id):
        raise client_error

    monkeypatch.setattr("app.services.cost_summary_service.get_ec2_other_breakdown", _raise_client_error)

    response = client.get(
        f"/api/v1/tenants/{tenant_id}/cloud-accounts/{cloud_account_id}/cost-breakdown/ec2-other",
        headers=h,
    )

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["error_type"] == "cost_snapshot_unavailable"


@patch("app.services.cloud_account_service.aws_validation_service.validate_cloud_account_role")
def test_test_connection_success(mock_validate, client, dev_org_scope):
    from app.services.aws_validation_service import AwsValidationResult

    mock_validate.return_value = AwsValidationResult(
        success=True,
        aws_account_id="123456789012",
        arn="arn:aws:sts::123456789012:assumed-role/X/y",
        user_id="AIDACKCEVSQ6C2EXAMPLE",
    )
    h = dev_org_scope["headers"]
    tenant_id = _create_tenant(client, dev_org_scope)
    response = client.post(
        f"/api/v1/tenants/{tenant_id}/cloud-accounts/test-connection",
        headers=h,
        json={
            "account_id": "123456789012",
            "role_arn": "arn:aws:iam::123456789012:role/OptimizationRole",
            "region_default": "us-east-1",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["aws_account_id"] == "123456789012"


def test_test_connection_invalid_payload(client, dev_org_scope):
    h = dev_org_scope["headers"]
    tenant_id = _create_tenant(client, dev_org_scope)
    response = client.post(
        f"/api/v1/tenants/{tenant_id}/cloud-accounts/test-connection",
        headers=h,
        json={
            "account_id": "not-twelve-digits",
            "role_arn": "arn:aws:iam::123456789012:role/OptimizationRole",
            "region_default": "us-east-1",
        },
    )
    assert response.status_code == 422


def test_test_connection_forbidden_viewer(client, dev_org_scope):
    tenant_id = _create_tenant(client, dev_org_scope)
    h = {**dev_org_scope["headers"], "X-Role": "viewer", "X-User": "view-only"}
    response = client.post(
        f"/api/v1/tenants/{tenant_id}/cloud-accounts/test-connection",
        headers=h,
        json={
            "account_id": "123456789012",
            "role_arn": "arn:aws:iam::123456789012:role/OptimizationRole",
            "region_default": "us-east-1",
        },
    )
    assert response.status_code == 403


def test_create_cloud_account_forbidden_viewer(client, dev_org_scope):
    tenant_id = _create_tenant(client, dev_org_scope)
    h = {**dev_org_scope["headers"], "X-Role": "viewer", "X-User": "view-only"}
    response = client.post(
        f"/api/v1/tenants/{tenant_id}/cloud-accounts",
        headers=h,
        json={
            "account_id": "123456789012",
            "name": "n",
            "role_arn": "arn:aws:iam::123456789012:role/OptimizationRole",
            "region_default": "us-east-1",
        },
    )
    assert response.status_code == 403
