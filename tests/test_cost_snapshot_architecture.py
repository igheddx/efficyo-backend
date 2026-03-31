from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from app.core.db import utc_now
from app.cost.guards import CostQuotaExceededError
from app.cost.service import cost_snapshot_service
from app.models.cloud_account import CloudAccount
from app.models.cost_snapshot import CostApiUsageLog, CostSnapshot, CostSyncPolicy
from app.models.tenant import Tenant
from app.services import copilot_context_service, cost_summary_service, summary_service, trend_service


def _seed_scope(db, dev_org_scope):
    org = dev_org_scope["org"]
    tenant = Tenant(name=f"tenant-{uuid4().hex[:8]}", status="active", organization_id=org.id)
    db.add(tenant)
    db.flush()
    account = CloudAccount(
        tenant_id=tenant.id,
        account_id="123456789012",
        name="acct",
        status="active",
        role_arn="arn:aws:iam::123456789012:role/OptimizationRole",
        region_default="us-east-1",
    )
    db.add(account)
    db.flush()
    return tenant, account


def test_cost_get_summary_uses_snapshots_only(client, db, dev_org_scope, monkeypatch):
    tenant, account = _seed_scope(db, dev_org_scope)
    db.add(
        CostSnapshot(
            org_id=dev_org_scope["org"].id,
            tenant_id=tenant.id,
            cloud_account_id=account.id,
            provider="aws",
            snapshot_date=utc_now().date(),
            period_start=(utc_now() - timedelta(days=30)).date(),
            period_end=utc_now().date(),
            granularity="DAILY",
            total_cost=123.45,
            currency="USD",
            service_breakdown_json=[{"service": "AmazonEC2", "amount": 120.0}],
            daily_costs_json=[{"date": utc_now().date().isoformat(), "total_cost": 4.1}],
            cost_trends_json=[
                {
                    "service": "AmazonEC2",
                    "trend": "stable",
                    "percent_change": 2.1,
                    "current_cost": 120.0,
                    "previous_cost": 117.5,
                    "summary": "AmazonEC2 cost was stable week over week (within 15%)",
                }
            ],
            ec2_other_breakdown_json=[],
            waf_monthly_cost=0,
            freshness_status="fresh",
            stale_after_minutes=1440,
        )
    )
    db.commit()

    def _should_not_call(*_args, **_kwargs):
        raise AssertionError("live cost API should not be called in GET flow")

    monkeypatch.setattr("app.services.cost_explorer_service.fetch_cost_summary", _should_not_call)
    headers = dev_org_scope["headers"]
    res = client.get(
        f"/api/v1/cost/summary?tenant_id={tenant.id}&cloud_account_id={account.id}",
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total_cost"] == 123.45
    assert body["is_snapshot_missing"] is False

    # Legacy cloud-account endpoint must also stay snapshot-only.
    res2 = client.get(
        f"/api/v1/tenants/{tenant.id}/cloud-accounts/{account.id}/cost-summary",
        headers=headers,
    )
    assert res2.status_code == 200
    assert res2.json()["total_cost"] == 123.45

    # Additional GET/read flows must remain snapshot-only.
    res3 = client.get(
        f"/api/v1/cost/trends?tenant_id={tenant.id}&cloud_account_id={account.id}&days=14",
        headers=headers,
    )
    assert res3.status_code == 200
    res4 = client.get(
        f"/api/v1/tenants/{tenant.id}/cloud-accounts/{account.id}/trends",
        headers=headers,
    )
    assert res4.status_code == 200
    res5 = client.get(
        f"/api/v1/tenants/{tenant.id}/cloud-accounts/{account.id}/cost-trends?days=14",
        headers=headers,
    )
    assert res5.status_code == 200
    res6 = client.get(
        f"/api/v1/tenants/{tenant.id}/cloud-accounts/{account.id}/cost-breakdown/ec2-other",
        headers=headers,
    )
    assert res6.status_code == 200
    res7 = client.get(
        f"/api/v1/cost/freshness?tenant_id={tenant.id}&cloud_account_id={account.id}",
        headers=headers,
    )
    assert res7.status_code == 200


def test_cost_freshness_marks_stale(client, db, dev_org_scope):
    tenant, account = _seed_scope(db, dev_org_scope)
    old = utc_now() - timedelta(days=3)
    row = CostSnapshot(
        org_id=dev_org_scope["org"].id,
        tenant_id=tenant.id,
        cloud_account_id=account.id,
        provider="aws",
        snapshot_date=old.date(),
        period_start=(old - timedelta(days=30)).date(),
        period_end=old.date(),
        granularity="DAILY",
        total_cost=10,
        currency="USD",
        service_breakdown_json=[],
        daily_costs_json=[],
        cost_trends_json=[],
        ec2_other_breakdown_json=[],
        waf_monthly_cost=0,
        freshness_status="fresh",
        stale_after_minutes=60,
    )
    row.updated_at = old
    db.add(row)
    db.commit()

    headers = dev_org_scope["headers"]
    res = client.get(
        f"/api/v1/cost/freshness?tenant_id={tenant.id}&cloud_account_id={account.id}",
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["is_stale"] is True


def test_cost_sync_creates_snapshot_and_usage_log(db, dev_org_scope, monkeypatch):
    tenant, account = _seed_scope(db, dev_org_scope)

    monkeypatch.setattr(
        "app.services.cost_explorer_service.fetch_cost_summary",
        lambda role_arn: {
            "start_date": (utc_now() - timedelta(days=30)).date().isoformat(),
            "end_date": utc_now().date().isoformat(),
            "total_cost": 50.0,
            "currency": "USD",
            "by_service": [{"service": "AmazonEC2", "amount": 50.0}],
        },
    )
    monkeypatch.setattr(
        "app.services.cost_explorer_service.fetch_daily_unblended_cost_by_service",
        lambda role_arn, days: [{"date": utc_now().date().isoformat(), "by_service": {"AmazonEC2": 2.0}}],
    )
    monkeypatch.setattr(
        "app.services.cost_explorer_service.fetch_ec2_other_breakdown",
        lambda role_arn: {"ec2_other_total": 0.0, "breakdown": []},
    )

    snap = cost_snapshot_service.sync_cost_snapshot(
        db,
        tenant_id=tenant.id,
        cloud_account_id=account.id,
        feature_name="test_sync",
        request_type="test",
        force_refresh=True,
        actor_is_system=True,
    )
    db.commit()
    assert snap.id is not None
    usage_count = (
        db.query(CostApiUsageLog)
        .filter(
            CostApiUsageLog.tenant_id == tenant.id,
            CostApiUsageLog.cloud_account_id == account.id,
        )
        .count()
    )
    assert usage_count >= 3


def test_cost_sync_quota_exceeded_blocks_live_fetch(db, dev_org_scope):
    tenant, account = _seed_scope(db, dev_org_scope)
    db.add(
        CostSyncPolicy(
            org_id=dev_org_scope["org"].id,
            tenant_id=tenant.id,
            cloud_account_id=account.id,
            provider="aws",
            max_calls_per_day=0,
            max_calls_per_job=0,
        )
    )
    db.commit()

    with pytest.raises(CostQuotaExceededError):
        cost_snapshot_service.sync_cost_snapshot(
            db,
            tenant_id=tenant.id,
            cloud_account_id=account.id,
            feature_name="quota_test",
            request_type="scheduled_sync",
            force_refresh=True,
            actor_is_system=True,
        )


def test_duplicate_fetch_in_progress_returns_last_snapshot(db, dev_org_scope, monkeypatch):
    tenant, account = _seed_scope(db, dev_org_scope)
    existing = CostSnapshot(
        org_id=dev_org_scope["org"].id,
        tenant_id=tenant.id,
        cloud_account_id=account.id,
        provider="aws",
        snapshot_date=utc_now().date(),
        period_start=(utc_now() - timedelta(days=30)).date(),
        period_end=utc_now().date(),
        granularity="DAILY",
        total_cost=77.7,
        currency="USD",
        service_breakdown_json=[{"service": "AmazonS3", "amount": 77.7}],
        daily_costs_json=[],
        cost_trends_json=[],
        ec2_other_breakdown_json=[],
        waf_monthly_cost=0,
        freshness_status="stale",
        stale_after_minutes=1,
    )
    existing.updated_at = utc_now() - timedelta(days=2)
    db.add(existing)
    db.commit()

    monkeypatch.setattr("app.cost.repository.acquire_fetch_lock", lambda *args, **kwargs: False)
    snap = cost_snapshot_service.sync_cost_snapshot(
        db,
        tenant_id=tenant.id,
        cloud_account_id=account.id,
        feature_name="dup_test",
        request_type="scheduled_sync",
        force_refresh=True,
        actor_is_system=True,
    )
    assert float(snap.total_cost) == 77.7


def test_org_daily_quota_exceeded_blocks_live_fetch(db, dev_org_scope):
    tenant, account = _seed_scope(db, dev_org_scope)
    db.add(
        CostSyncPolicy(
            org_id=dev_org_scope["org"].id,
            provider="aws",
            max_calls_per_org_day=0,
            max_calls_per_day=50,
            max_calls_per_job=50,
        )
    )
    db.commit()
    with pytest.raises(CostQuotaExceededError, match="org_daily_quota_exceeded"):
        cost_snapshot_service.sync_cost_snapshot(
            db,
            tenant_id=tenant.id,
            cloud_account_id=account.id,
            feature_name="org_quota_test",
            request_type="scheduled_sync",
            force_refresh=True,
            actor_is_system=True,
        )


def test_org_quota_exceeded_falls_back_to_stale_snapshot(db, dev_org_scope):
    tenant, account = _seed_scope(db, dev_org_scope)
    existing = CostSnapshot(
        org_id=dev_org_scope["org"].id,
        tenant_id=tenant.id,
        cloud_account_id=account.id,
        provider="aws",
        snapshot_date=utc_now().date(),
        period_start=(utc_now() - timedelta(days=30)).date(),
        period_end=utc_now().date(),
        granularity="DAILY",
        total_cost=22.2,
        currency="USD",
        service_breakdown_json=[],
        daily_costs_json=[],
        cost_trends_json=[],
        ec2_other_breakdown_json=[],
        waf_monthly_cost=0,
        freshness_status="stale",
        stale_after_minutes=1,
    )
    existing.updated_at = utc_now() - timedelta(days=2)
    db.add(existing)
    db.add(
        CostSyncPolicy(
            org_id=dev_org_scope["org"].id,
            provider="aws",
            max_calls_per_org_day=0,
            max_calls_per_day=50,
            max_calls_per_job=50,
        )
    )
    db.commit()
    snap = cost_snapshot_service.sync_cost_snapshot(
        db,
        tenant_id=tenant.id,
        cloud_account_id=account.id,
        feature_name="org_quota_fallback",
        request_type="scheduled_sync",
        force_refresh=True,
        actor_is_system=True,
    )
    assert float(snap.total_cost) == 22.2


def test_org_quota_does_not_block_snapshot_reads(client, db, dev_org_scope):
    tenant, account = _seed_scope(db, dev_org_scope)
    db.add(
        CostSnapshot(
            org_id=dev_org_scope["org"].id,
            tenant_id=tenant.id,
            cloud_account_id=account.id,
            provider="aws",
            snapshot_date=utc_now().date(),
            period_start=(utc_now() - timedelta(days=30)).date(),
            period_end=utc_now().date(),
            granularity="DAILY",
            total_cost=44.4,
            currency="USD",
            service_breakdown_json=[],
            daily_costs_json=[],
            cost_trends_json=[],
            ec2_other_breakdown_json=[],
            waf_monthly_cost=0,
            freshness_status="fresh",
            stale_after_minutes=1440,
        )
    )
    db.add(CostSyncPolicy(org_id=dev_org_scope["org"].id, provider="aws", max_calls_per_org_day=0))
    db.commit()
    res = client.get(
        f"/api/v1/cost/summary?tenant_id={tenant.id}&cloud_account_id={account.id}",
        headers=dev_org_scope["headers"],
    )
    assert res.status_code == 200
    assert res.json()["total_cost"] == 44.4


def test_force_refresh_forbidden_for_viewer_no_live_call(db, dev_org_scope, monkeypatch):
    tenant, account = _seed_scope(db, dev_org_scope)

    def _should_not_call(*_args, **_kwargs):
        raise AssertionError("live cost API should not be called for unauthorized force refresh")

    monkeypatch.setattr("app.services.cost_explorer_service.fetch_cost_summary", _should_not_call)
    with pytest.raises(CostQuotaExceededError, match="force_refresh_forbidden"):
        cost_snapshot_service.sync_cost_snapshot(
            db,
            tenant_id=tenant.id,
            cloud_account_id=account.id,
            feature_name="force_refresh_test",
            request_type="manual_sync",
            force_refresh=True,
            actor_role="viewer",
        )


def test_force_refresh_forbidden_for_approver_no_live_call(db, dev_org_scope, monkeypatch):
    tenant, account = _seed_scope(db, dev_org_scope)

    def _should_not_call(*_args, **_kwargs):
        raise AssertionError("live cost API should not be called for unauthorized force refresh")

    monkeypatch.setattr("app.services.cost_explorer_service.fetch_cost_summary", _should_not_call)
    with pytest.raises(CostQuotaExceededError, match="force_refresh_forbidden"):
        cost_snapshot_service.sync_cost_snapshot(
            db,
            tenant_id=tenant.id,
            cloud_account_id=account.id,
            feature_name="force_refresh_test",
            request_type="manual_sync",
            force_refresh=True,
            actor_role="approver",
        )


def test_admin_force_refresh_allowed_when_policy_permits(db, dev_org_scope, monkeypatch):
    tenant, account = _seed_scope(db, dev_org_scope)
    db.add(
        CostSyncPolicy(
            org_id=dev_org_scope["org"].id,
            tenant_id=tenant.id,
            cloud_account_id=account.id,
            provider="aws",
            allow_admin_force_refresh=True,
        )
    )
    db.commit()
    monkeypatch.setattr(
        "app.services.cost_explorer_service.fetch_cost_summary",
        lambda role_arn: {
            "start_date": (utc_now() - timedelta(days=30)).date().isoformat(),
            "end_date": utc_now().date().isoformat(),
            "total_cost": 10.0,
            "currency": "USD",
            "by_service": [{"service": "AmazonEC2", "amount": 10.0}],
        },
    )
    monkeypatch.setattr(
        "app.services.cost_explorer_service.fetch_daily_unblended_cost_by_service",
        lambda role_arn, days: [{"date": utc_now().date().isoformat(), "by_service": {"AmazonEC2": 1.0}}],
    )
    monkeypatch.setattr(
        "app.services.cost_explorer_service.fetch_ec2_other_breakdown",
        lambda role_arn: {"ec2_other_total": 0.0, "breakdown": []},
    )
    snap = cost_snapshot_service.sync_cost_snapshot(
        db,
        tenant_id=tenant.id,
        cloud_account_id=account.id,
        feature_name="force_refresh_admin",
        request_type="manual_sync",
        force_refresh=True,
        actor_role="admin",
    )
    assert float(snap.total_cost) == 10.0


def test_root_force_refresh_allowed(db, dev_org_scope, monkeypatch):
    tenant, account = _seed_scope(db, dev_org_scope)
    monkeypatch.setattr(
        "app.services.cost_explorer_service.fetch_cost_summary",
        lambda role_arn: {
            "start_date": (utc_now() - timedelta(days=30)).date().isoformat(),
            "end_date": utc_now().date().isoformat(),
            "total_cost": 12.0,
            "currency": "USD",
            "by_service": [{"service": "AmazonEC2", "amount": 12.0}],
        },
    )
    monkeypatch.setattr(
        "app.services.cost_explorer_service.fetch_daily_unblended_cost_by_service",
        lambda role_arn, days: [{"date": utc_now().date().isoformat(), "by_service": {"AmazonEC2": 1.2}}],
    )
    monkeypatch.setattr(
        "app.services.cost_explorer_service.fetch_ec2_other_breakdown",
        lambda role_arn: {"ec2_other_total": 0.0, "breakdown": []},
    )
    snap = cost_snapshot_service.sync_cost_snapshot(
        db,
        tenant_id=tenant.id,
        cloud_account_id=account.id,
        feature_name="force_refresh_root",
        request_type="manual_sync",
        force_refresh=True,
        actor_is_platform_root=True,
    )
    assert float(snap.total_cost) == 12.0


def test_snapshot_source_semantics_for_fresh_and_stale(client, db, dev_org_scope):
    tenant, account = _seed_scope(db, dev_org_scope)
    now = utc_now()
    row = CostSnapshot(
        org_id=dev_org_scope["org"].id,
        tenant_id=tenant.id,
        cloud_account_id=account.id,
        provider="aws",
        snapshot_date=now.date(),
        period_start=(now - timedelta(days=30)).date(),
        period_end=now.date(),
        granularity="DAILY",
        total_cost=9.9,
        currency="USD",
        service_breakdown_json=[],
        daily_costs_json=[],
        cost_trends_json=[],
        ec2_other_breakdown_json=[],
        waf_monthly_cost=0,
        freshness_status="fresh",
        stale_after_minutes=1440,
    )
    db.add(row)
    db.commit()
    res_fresh = client.get(
        f"/api/v1/cost/summary?tenant_id={tenant.id}&cloud_account_id={account.id}",
        headers=dev_org_scope["headers"],
    )
    assert res_fresh.status_code == 200
    assert res_fresh.json()["data_source"] == "snapshot_hit"

    row.updated_at = utc_now() - timedelta(days=3)
    row.stale_after_minutes = 1
    db.add(row)
    db.commit()
    res_stale = client.get(
        f"/api/v1/cost/summary?tenant_id={tenant.id}&cloud_account_id={account.id}",
        headers=dev_org_scope["headers"],
    )
    assert res_stale.status_code == 200
    assert res_stale.json()["data_source"] == "stale_snapshot_served"


def test_dashboard_related_services_stay_snapshot_only(db, dev_org_scope, monkeypatch):
    tenant, account = _seed_scope(db, dev_org_scope)
    db.add(
        CostSnapshot(
            org_id=dev_org_scope["org"].id,
            tenant_id=tenant.id,
            cloud_account_id=account.id,
            provider="aws",
            snapshot_date=utc_now().date(),
            period_start=(utc_now() - timedelta(days=30)).date(),
            period_end=utc_now().date(),
            granularity="DAILY",
            total_cost=88.8,
            currency="USD",
            service_breakdown_json=[{"service": "AmazonEC2", "amount": 88.8}],
            daily_costs_json=[{"date": utc_now().date().isoformat(), "total_cost": 2.2}],
            cost_trends_json=[{"service": "AmazonEC2", "trend": "stable", "percent_change": 0.0}],
            ec2_other_breakdown_json=[],
            waf_monthly_cost=0,
            freshness_status="fresh",
            stale_after_minutes=1440,
        )
    )
    db.commit()

    def _should_not_call(*_args, **_kwargs):
        raise AssertionError("live cost API should not be called in dashboard/cost context services")

    monkeypatch.setattr("app.services.cost_explorer_service.fetch_cost_summary", _should_not_call)
    monkeypatch.setattr("app.services.cost_explorer_service.fetch_daily_unblended_cost_by_service", _should_not_call)
    monkeypatch.setattr("app.services.cost_explorer_service.fetch_ec2_other_breakdown", _should_not_call)

    s = cost_summary_service.get_cost_summary(db, tenant.id, account.id)
    assert s["total_cost"] == 88.8
    t = trend_service.cost_trends_over_time(db, tenant.id, account.id, days=7)
    assert "points" in t
    summary = summary_service.get_cloud_account_summary(db, tenant.id, account.id)
    assert summary.total_cost == 88.8
    copilot = copilot_context_service.build_scoped_copilot_context(
        db,
        intent="savings",
        organization_id=dev_org_scope["org"].id,
        tenant_id=tenant.id,
        cloud_account_id=account.id,
        effective_operational_access="admin",
        org_membership_role="admin",
    )
    assert "summary" in copilot


def test_usage_aggregation_endpoints(client, db, dev_org_scope):
    tenant, account = _seed_scope(db, dev_org_scope)
    now = utc_now()
    db.add_all(
        [
            CostApiUsageLog(
                org_id=dev_org_scope["org"].id,
                tenant_id=tenant.id,
                cloud_account_id=account.id,
                provider="aws",
                feature_name="cost_sync_job",
                request_type="scheduled_sync",
                request_signature="sig-1",
                was_cache_hit=False,
                api_name="ce.get_cost_and_usage.summary",
                estimated_call_cost=0.01,
                created_at=now,
            ),
            CostApiUsageLog(
                org_id=dev_org_scope["org"].id,
                tenant_id=tenant.id,
                cloud_account_id=account.id,
                provider="aws",
                feature_name="dashboard_read",
                request_type="snapshot_read",
                request_signature="sig-2",
                was_cache_hit=True,
                api_name="snapshot.read",
                estimated_call_cost=0.0,
                created_at=now,
            ),
        ]
    )
    db.commit()
    headers = dev_org_scope["headers"]
    s = client.get(
        f"/api/v1/cost/usage/summary?tenant_id={tenant.id}&cloud_account_id={account.id}",
        headers=headers,
    )
    assert s.status_code == 200
    assert s.json()["total_calls"] >= 2
    assert s.json()["live_calls"] >= 1
    assert s.json()["cache_hits"] >= 1

    by_feature = client.get(
        f"/api/v1/cost/usage/by-feature?tenant_id={tenant.id}&cloud_account_id={account.id}",
        headers=headers,
    )
    assert by_feature.status_code == 200
    assert any(i["group_key"] == "cost_sync_job" for i in by_feature.json()["items"])

