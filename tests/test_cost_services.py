from datetime import date
from uuid import uuid4

import pytest

from app.models.cloud_account import CloudAccount
from app.models.tenant import Tenant
from app.core.cost_window import account_cost_window_fields
from app.services import cost_explorer_service, cost_summary_service

_FIXED_CE_TODAY = date(2026, 3, 25)


def _patch_ce_today(monkeypatch):
    monkeypatch.setattr("app.core.cost_window.utc_today", lambda: _FIXED_CE_TODAY)


class _FakeCostExplorerClient:
    def __init__(self, response_payload: dict | list[dict]):
        self._pages: list[dict] = response_payload if isinstance(response_payload, list) else [response_payload]
        self.calls: list[dict] = []
        self._idx = 0

    def get_cost_and_usage(self, **kwargs):
        self.calls.append(kwargs)
        if self._idx >= len(self._pages):
            raise AssertionError("unexpected extra Cost Explorer page request")
        page = self._pages[self._idx]
        self._idx += 1
        return page


def test_fetch_cost_summary_aggregates_rounds_sorts_and_uses_rolling_window(monkeypatch):
    _patch_ce_today(monkeypatch)

    monkeypatch.setattr(
        cost_explorer_service.aws_assume_role_service,
        "assume_role",
        lambda role_arn, region, session_name: {
            "AccessKeyId": "key",
            "SecretAccessKey": "secret",
            "SessionToken": "token",
        },
    )

    fake_response = {
        "ResultsByTime": [
            {
                "Groups": [
                    {
                        "Keys": ["Amazon Relational Database Service"],
                        "Metrics": {"UnblendedCost": {"Amount": "10.115"}},
                    },
                    {
                        "Keys": ["AWS Lambda"],
                        "Metrics": {"UnblendedCost": {"Amount": "3.335"}},
                    },
                ]
            },
            {
                "Groups": [
                    {
                        "Keys": ["Amazon Relational Database Service"],
                        "Metrics": {"UnblendedCost": {"Amount": "10.115"}},
                    },
                    {
                        "Keys": ["Amazon Simple Storage Service"],
                        "Metrics": {"UnblendedCost": {"Amount": "20.23"}},
                    },
                ]
            },
        ]
    }
    fake_client = _FakeCostExplorerClient(fake_response)

    boto3_calls: list[dict] = []

    def _fake_boto3_client(service_name, **kwargs):
        boto3_calls.append({"service_name": service_name, **kwargs})
        assert service_name == "ce"
        return fake_client

    monkeypatch.setattr(cost_explorer_service.boto3, "client", _fake_boto3_client)

    result = cost_explorer_service.fetch_cost_summary("arn:aws:iam::123456789012:role/OptimizationRole")

    assert result["start_date"] == "2026-02-23"
    assert result["end_date"] == "2026-03-25"
    assert result["total_cost"] == 43.8
    assert result["by_service"] == [
        {"service": "Amazon Relational Database Service", "amount": 20.23},
        {"service": "Amazon Simple Storage Service", "amount": 20.23},
        {"service": "AWS Lambda", "amount": 3.34},
    ]

    assert len(boto3_calls) == 1
    assert boto3_calls[0]["region_name"] == "us-east-1"
    assert boto3_calls[0]["aws_access_key_id"] == "key"
    assert len(fake_client.calls) == 1
    ce_call = fake_client.calls[0]
    assert ce_call["TimePeriod"] == {"Start": "2026-02-23", "End": "2026-03-25"}
    assert ce_call["Granularity"] == "DAILY"
    assert ce_call["Metrics"] == ["UnblendedCost"]
    assert ce_call["GroupBy"] == [{"Type": "DIMENSION", "Key": "SERVICE"}]
    assert result["cost_window"] == account_cost_window_fields()["cost_window"]
    assert result["cost_window_label"] == account_cost_window_fields()["cost_window_label"]
    assert result["cost_metric"] == account_cost_window_fields()["cost_metric"]


def test_fetch_cost_summary_handles_empty_results(monkeypatch):
    _patch_ce_today(monkeypatch)
    monkeypatch.setattr(
        cost_explorer_service.aws_assume_role_service,
        "assume_role",
        lambda role_arn, region, session_name: {
            "AccessKeyId": "key",
            "SecretAccessKey": "secret",
            "SessionToken": "token",
        },
    )

    fake_client = _FakeCostExplorerClient({"ResultsByTime": []})
    monkeypatch.setattr(cost_explorer_service.boto3, "client", lambda *_args, **_kwargs: fake_client)

    result = cost_explorer_service.fetch_cost_summary("arn:aws:iam::123456789012:role/OptimizationRole")

    assert result == {
        "start_date": "2026-02-23",
        "end_date": "2026-03-25",
        "total_cost": 0.0,
        "by_service": [],
        **account_cost_window_fields(),
    }


def test_fetch_cost_summary_paginates_and_merges_pages(monkeypatch):
    _patch_ce_today(monkeypatch)
    monkeypatch.setattr(
        cost_explorer_service.aws_assume_role_service,
        "assume_role",
        lambda role_arn, region, session_name: {
            "AccessKeyId": "key",
            "SecretAccessKey": "secret",
            "SessionToken": "token",
        },
    )

    page1 = {
        "ResultsByTime": [
            {
                "Groups": [
                    {
                        "Keys": ["Amazon Simple Storage Service"],
                        "Metrics": {"UnblendedCost": {"Amount": "7.50"}},
                    },
                ]
            }
        ],
        "NextPageToken": "next-page",
    }
    page2 = {
        "ResultsByTime": [
            {
                "Groups": [
                    {
                        "Keys": ["Amazon Simple Storage Service"],
                        "Metrics": {"UnblendedCost": {"Amount": "2.50"}},
                    },
                    {
                        "Keys": ["AWS Lambda"],
                        "Metrics": {"UnblendedCost": {"Amount": "3.005"}},
                    },
                ]
            }
        ],
    }
    fake_client = _FakeCostExplorerClient([page1, page2])
    monkeypatch.setattr(cost_explorer_service.boto3, "client", lambda *_args, **_kwargs: fake_client)

    result = cost_explorer_service.fetch_cost_summary("arn:aws:iam::123456789012:role/OptimizationRole")

    assert result["total_cost"] == 13.01
    assert result["cost_window"] == "rolling_30d"
    assert result["cost_metric"] == account_cost_window_fields()["cost_metric"]
    assert result["by_service"] == [
        {"service": "Amazon Simple Storage Service", "amount": 10.0},
        {"service": "AWS Lambda", "amount": 3.01},
    ]
    assert len(fake_client.calls) == 2
    assert "NextPageToken" not in fake_client.calls[0]
    assert fake_client.calls[1]["NextPageToken"] == "next-page"


def test_fetch_ec2_other_breakdown_groups_usage_types_into_expected_categories(monkeypatch):
    _patch_ce_today(monkeypatch)
    monkeypatch.setattr(
        cost_explorer_service.aws_assume_role_service,
        "assume_role",
        lambda role_arn, region, session_name: {
            "AccessKeyId": "key",
            "SecretAccessKey": "secret",
            "SessionToken": "token",
        },
    )

    fake_response = {
        "ResultsByTime": [
            {
                "Groups": [
                    {
                        "Keys": ["USE1-NatGateway-Hours"],
                        "Metrics": {"UnblendedCost": {"Amount": "5.001"}},
                    },
                    {
                        "Keys": ["EBS:SnapshotUsage"],
                        "Metrics": {"UnblendedCost": {"Amount": "2.004"}},
                    },
                    {
                        "Keys": ["USE2-DataTransfer-Out-Bytes"],
                        "Metrics": {"UnblendedCost": {"Amount": "1.333"}},
                    },
                    {
                        "Keys": ["USE1-VpcEndpoint-Hours"],
                        "Metrics": {"UnblendedCost": {"Amount": "0.999"}},
                    },
                ]
            }
        ]
    }
    fake_client = _FakeCostExplorerClient(fake_response)

    monkeypatch.setattr(cost_explorer_service.boto3, "client", lambda *_args, **_kwargs: fake_client)

    result = cost_explorer_service.fetch_ec2_other_breakdown(
        "arn:aws:iam::123456789012:role/OptimizationRole"
    )

    assert result["ec2_other_total"] == 9.34
    assert result["breakdown"] == [
        {"category": "NAT Gateway", "amount": 5.0},
        {"category": "EBS Snapshots", "amount": 2.0},
        {"category": "Data Transfer", "amount": 1.33},
        {"category": "Other", "amount": 1.0},
    ]

    assert len(fake_client.calls) == 1
    ce_call = fake_client.calls[0]
    assert ce_call["TimePeriod"] == {"Start": "2026-02-23", "End": "2026-03-25"}
    assert ce_call["Granularity"] == "DAILY"
    assert ce_call["Metrics"] == ["UnblendedCost"]
    assert ce_call["GroupBy"] == [{"Type": "DIMENSION", "Key": "USAGE_TYPE"}]
    assert ce_call["Filter"] == {
        "Dimensions": {
            "Key": "SERVICE",
            "Values": ["EC2 - Other"],
        }
    }


def test_fetch_ec2_other_breakdown_handles_empty_results(monkeypatch):
    _patch_ce_today(monkeypatch)
    monkeypatch.setattr(
        cost_explorer_service.aws_assume_role_service,
        "assume_role",
        lambda role_arn, region, session_name: {
            "AccessKeyId": "key",
            "SecretAccessKey": "secret",
            "SessionToken": "token",
        },
    )

    fake_client = _FakeCostExplorerClient({"ResultsByTime": []})
    monkeypatch.setattr(cost_explorer_service.boto3, "client", lambda *_args, **_kwargs: fake_client)

    result = cost_explorer_service.fetch_ec2_other_breakdown(
        "arn:aws:iam::123456789012:role/OptimizationRole"
    )

    assert result == {
        "ec2_other_total": 0.0,
        "breakdown": [],
    }


def test_get_cost_summary_returns_service_data_for_scoped_account(db, monkeypatch):
    tenant = Tenant(name="cost-tenant")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    cloud_account = CloudAccount(
        tenant_id=tenant.id,
        account_id="123456789012",
        name="primary",
        status="connected",
        role_arn="arn:aws:iam::123456789012:role/OptimizationRole",
        region_default="us-east-1",
    )
    db.add(cloud_account)
    db.commit()
    db.refresh(cloud_account)

    observed = {"role_arn": None}

    def _fake_fetch(role_arn):
        observed["role_arn"] = role_arn
        return {
            "start_date": "2026-02-23",
            "end_date": "2026-03-25",
            "total_cost": 12.34,
            "by_service": [{"service": "AWS Lambda", "amount": 12.34}],
        }

    monkeypatch.setattr(cost_summary_service.cost_explorer_service, "fetch_cost_summary", _fake_fetch)

    result = cost_summary_service.get_cost_summary(db, tenant.id, cloud_account.id)

    assert observed["role_arn"] == "arn:aws:iam::123456789012:role/OptimizationRole"
    assert result["total_cost"] == 12.34
    assert result["by_service"][0]["service"] == "AWS Lambda"
    assert result["cost_window"] == account_cost_window_fields()["cost_window"]


def test_get_cost_summary_raises_for_missing_tenant(db):
    with pytest.raises(ValueError, match="tenant_not_found"):
        cost_summary_service.get_cost_summary(db, uuid4(), uuid4())


def test_get_cost_summary_raises_for_missing_cloud_account(db):
    tenant = Tenant(name="tenant-only")
    db.add(tenant)
    db.commit()

    with pytest.raises(ValueError, match="cloud_account_not_found"):
        cost_summary_service.get_cost_summary(db, tenant.id, uuid4())
