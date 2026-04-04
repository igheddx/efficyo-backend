"""Tests for week-over-week cost trend detection (DAILY + SERVICE, last 14 days)."""

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.cloud_account import CloudAccount
from app.models.recommendation import Recommendation
from app.models.recommendation_outcome import RecommendationOutcome
from app.models.tenant import Tenant
from app.services import cost_explorer_service, trend_service

_FIXED_CE_TODAY = date(2026, 3, 25)


def _patch_ce_today(monkeypatch):
    monkeypatch.setattr("app.services.trend_service.utc_today", lambda: _FIXED_CE_TODAY)


def _fake_daily_rows_ec2_spike():
    """14 days: first 7 days sum to 100 EC2, last 7 sum to 120 EC2 (+20% week over week)."""
    rows = []
    for i in range(7):
        rows.append({"date": f"2026-03-{11 + i:02d}", "by_service": {"EC2 - Other": Decimal("100") / Decimal("7")}})
    for i in range(7):
        rows.append({"date": f"2026-03-{18 + i:02d}", "by_service": {"EC2 - Other": Decimal("120") / Decimal("7")}})
    return rows


def test_detect_cost_trends_week_over_week_increasing(db, monkeypatch):
    _patch_ce_today(monkeypatch)

    tid = uuid4()
    tenant = Tenant(id=tid, name=f"trend-test-{tid}")
    db.add(tenant)
    db.commit()

    cloud = CloudAccount(
        id=uuid4(),
        tenant_id=tenant.id,
        account_id="123456789012",
        name="acct",
        status="active",
        role_arn="arn:aws:iam::123456789012:role/OptimizationRole",
        region_default="us-east-1",
    )
    db.add(cloud)
    db.commit()

    monkeypatch.setattr(
        "app.cost.query_service.get_wow_trends",
        lambda _db, _tenant_id, _cloud_id: [
            {
                "service": "EC2 - Other",
                "previous_cost": 100.0,
                "current_cost": 120.0,
                "percent_change": 20.0,
                "trend": "increasing",
                "summary": "EC2 - Other cost increased by 20% over the last week",
            }
        ],
    )

    rows = trend_service.detect_cost_trends(db, tenant.id, cloud.id)
    ec2 = next(r for r in rows if r["service"] == "EC2 - Other")
    assert ec2["trend"] == "increasing"
    assert ec2["previous_cost"] == pytest.approx(100.0, rel=1e-2)
    assert ec2["current_cost"] == pytest.approx(120.0, rel=1e-2)
    assert ec2["percent_change"] == pytest.approx(20.0, rel=1e-2)
    assert "increased" in ec2["summary"].lower()


def test_fetch_daily_merges_pagination(monkeypatch):
    _patch_ce_today(monkeypatch)
    monkeypatch.setattr(
        cost_explorer_service.aws_assume_role_service,
        "assume_role",
        lambda role_arn, region, session_name, **kwargs: {
            "AccessKeyId": "key",
            "SecretAccessKey": "secret",
            "SessionToken": "token",
        },
    )

    responses = [
        {
            "ResultsByTime": [
                {
                    "TimePeriod": {"Start": "2026-03-11", "End": "2026-03-12"},
                    "Groups": [
                        {"Keys": ["AWS Lambda"], "Metrics": {"UnblendedCost": {"Amount": "1"}}},
                    ],
                }
            ],
            "NextPageToken": "next",
        },
        {
            "ResultsByTime": [
                {
                    "TimePeriod": {"Start": "2026-03-11", "End": "2026-03-12"},
                    "Groups": [
                        {"Keys": ["AWS Lambda"], "Metrics": {"UnblendedCost": {"Amount": "2"}}},
                    ],
                }
            ],
        },
    ]

    def _fake_client(service_name, **kwargs):
        assert service_name == "ce"

        class _C:
            def __init__(self):
                self._i = 0

            def get_cost_and_usage(self, **params):
                r = responses[self._i]
                self._i += 1
                return r

        return _C()

    monkeypatch.setattr(cost_explorer_service.boto3, "client", _fake_client)

    rows = cost_explorer_service.fetch_daily_unblended_cost_by_service_last_14_days(
        "arn:aws:iam::123456789012:role/OptimizationRole"
    )
    day = next(x for x in rows if x["date"] == "2026-03-11")
    assert day["by_service"]["AWS Lambda"] == Decimal("3")


def test_cost_trends_over_time_returns_daily_totals(db, monkeypatch):
    tid = uuid4()
    tenant = Tenant(id=tid, name=f"trend-series-test-{tid}")
    db.add(tenant)
    db.commit()

    cloud = CloudAccount(
        id=uuid4(),
        tenant_id=tenant.id,
        account_id="123456789012",
        name="acct",
        status="active",
        role_arn="arn:aws:iam::123456789012:role/OptimizationRole",
        region_default="us-east-1",
    )
    db.add(cloud)
    db.commit()

    monkeypatch.setattr(
        "app.cost.query_service.get_cost_trends",
        lambda _db, _tenant_id, _cloud_id: {
            "points": [
                {"date": "2026-03-20", "total_cost": 4.0},
                {"date": "2026-03-21", "total_cost": 3.0},
            ]
        },
    )

    series = trend_service.cost_trends_over_time(db, tenant.id, cloud.id, days=30)
    assert series["days"] == 30
    assert series["cost_window"] == "rolling_30d"
    assert series["cost_window_label"] == "Rolling last 30 days"
    assert series["cost_metric"] == "UnblendedCost"
    assert series["points"] == [
        {"date": "2026-03-20", "total_cost": 4.0},
        {"date": "2026-03-21", "total_cost": 3.0},
    ]

    series14 = trend_service.cost_trends_over_time(db, tenant.id, cloud.id, days=14)
    assert series14["cost_window"] == "rolling_nd"
    assert series14["cost_window_label"] == "Rolling last 14 days"
    assert series14["cost_metric"] == "UnblendedCost"


def test_savings_trends_over_time_verified_only(db, monkeypatch):
    _patch_ce_today(monkeypatch)

    tid = uuid4()
    tenant = Tenant(id=tid, name=f"savings-series-test-{tid}")
    db.add(tenant)
    db.commit()

    cloud = CloudAccount(
        id=uuid4(),
        tenant_id=tenant.id,
        account_id="123456789012",
        name="acct",
        status="active",
        role_arn="arn:aws:iam::123456789012:role/OptimizationRole",
        region_default="us-east-1",
    )
    db.add(cloud)
    db.flush()

    rec = Recommendation(
        id=uuid4(),
        tenant_id=tenant.id,
        cloud_account_id=cloud.id,
        finding_id=uuid4(),
        resource_id="bucket-1",
        resource_type="s3_bucket",
        recommendation_type="s3_enable_public_access_block",
        recommendation_category="security",
        summary="Enable S3 Public Access Block",
        explanation="test",
        risk_level="high",
        recommended_action="test",
        confidence_score="high",
        created_at=datetime(2026, 3, 20, tzinfo=timezone.utc),
    )
    db.add(rec)
    db.flush()
    rec2 = Recommendation(
        id=uuid4(),
        tenant_id=tenant.id,
        cloud_account_id=cloud.id,
        finding_id=uuid4(),
        resource_id="bucket-2",
        resource_type="s3_bucket",
        recommendation_type="s3_add_required_tags",
        recommendation_category="governance",
        summary="Add required tags to S3 bucket",
        explanation="test",
        risk_level="medium",
        recommended_action="test",
        confidence_score="medium",
        created_at=datetime(2026, 3, 20, tzinfo=timezone.utc),
    )
    db.add(rec2)
    db.flush()

    verified = RecommendationOutcome(
        id=uuid4(),
        tenant_id=tenant.id,
        cloud_account_id=cloud.id,
        recommendation_id=rec.id,
        resource_id="bucket-1",
        recommendation_type="s3_enable_public_access_block",
        recommendation_category="security",
        status="verified",
        baseline_monthly_cost=Decimal("20.00"),
        current_monthly_cost=Decimal("15.00"),
        realized_savings=Decimal("5.00"),
        last_evaluated_at=datetime(2026, 3, 25, tzinfo=timezone.utc),
        created_at=datetime(2026, 3, 20, tzinfo=timezone.utc),
        updated_at=datetime(2026, 3, 25, tzinfo=timezone.utc),
    )
    not_verified = RecommendationOutcome(
        id=uuid4(),
        tenant_id=tenant.id,
        cloud_account_id=cloud.id,
        recommendation_id=rec2.id,
        resource_id="bucket-1",
        recommendation_type="s3_enable_public_access_block",
        recommendation_category="security",
        status="acted_on",
        realized_savings=Decimal("7.00"),
        last_evaluated_at=datetime(2026, 3, 24, tzinfo=timezone.utc),
        created_at=datetime(2026, 3, 20, tzinfo=timezone.utc),
        updated_at=datetime(2026, 3, 24, tzinfo=timezone.utc),
    )
    db.add_all([verified, not_verified])
    db.commit()

    series = trend_service.savings_trends_over_time(db, tenant.id, cloud.id, days=7)
    assert series["days"] == 7
    assert series["cost_window"] == "savings_outcomes_nd"
    assert "verified savings outcomes" in series["cost_window_label"]
    assert series["cost_metric"] == ""
    point = next(p for p in series["points"] if p["date"] == "2026-03-25")
    assert point["savings_realized"] == 5.0
    assert any(b["summary"] == "Enable S3 Public Access Block" for b in series["before_after"])
