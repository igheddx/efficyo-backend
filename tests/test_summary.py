from datetime import timedelta
from decimal import Decimal

from app.core.db import utc_now
from app.models.cloud_account import CloudAccount
from app.models.finding import Finding
from app.models.resource_snapshot import ResourceSnapshot
from app.models.recommendation import Recommendation
from app.models.tenant import Tenant


def test_summary_uses_latest_deduped_recommendations_only(client, db, dev_org_scope, monkeypatch):
    monkeypatch.setattr(
        "app.services.cost_explorer_service.fetch_cost_summary",
        lambda role_arn: {
            "start_date": "2026-02-23",
            "end_date": "2026-03-25",
            "total_cost": 100.0,
            "by_service": [
                {"service": "Amazon Relational Database Service", "amount": 50.0},
                {"service": "AWS Lambda", "amount": 30.0},
                {"service": "Amazon Simple Storage Service", "amount": 20.0},
                {"service": "Amazon EC2", "amount": 10.0},
            ],
        },
    )

    org = dev_org_scope["org"]
    tenant = Tenant(name="summary-tenant", organization_id=org.id)
    db.add(tenant)
    db.flush()

    cloud_account = CloudAccount(
        tenant_id=tenant.id,
        account_id="123456789012",
        name="summary-account",
        role_arn="arn:aws:iam::123456789012:role/OptimizationRole",
        region_default="us-east-1",
    )
    db.add(cloud_account)
    db.flush()

    now = utc_now()
    older = now - timedelta(days=1)

    security_snapshot = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=cloud_account.id,
        resource_id="bucket-1",
        resource_type="s3_bucket",
        region="us-east-1",
        configuration_json={},
        tags_json={},
        captured_at=older,
    )
    cost_snapshot = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=cloud_account.id,
        resource_id="cluster-1",
        resource_type="rds_cluster",
        region="us-east-1",
        configuration_json={},
        tags_json={},
        captured_at=older,
    )
    db.add_all([security_snapshot, cost_snapshot])
    db.flush()

    old_security_finding = Finding(
        tenant_id=tenant.id,
        cloud_account_id=cloud_account.id,
        resource_snapshot_id=security_snapshot.id,
        resource_id="bucket-1",
        resource_type="s3_bucket",
        finding_type="s3_public_access_candidate",
        severity="high",
        evidence_json={},
        detected_at=older,
    )
    current_security_finding = Finding(
        tenant_id=tenant.id,
        cloud_account_id=cloud_account.id,
        resource_snapshot_id=security_snapshot.id,
        resource_id="bucket-1",
        resource_type="s3_bucket",
        finding_type="s3_public_access_candidate",
        severity="high",
        evidence_json={},
        detected_at=now,
    )
    old_cost_finding = Finding(
        tenant_id=tenant.id,
        cloud_account_id=cloud_account.id,
        resource_snapshot_id=cost_snapshot.id,
        resource_id="cluster-1",
        resource_type="rds_cluster",
        finding_type="aurora_serverless_review_candidate",
        severity="medium",
        evidence_json={},
        estimated_savings=Decimal("100.00"),
        detected_at=older,
    )
    current_cost_finding = Finding(
        tenant_id=tenant.id,
        cloud_account_id=cloud_account.id,
        resource_snapshot_id=cost_snapshot.id,
        resource_id="cluster-1",
        resource_type="rds_cluster",
        finding_type="aurora_serverless_review_candidate",
        severity="medium",
        evidence_json={},
        estimated_savings=Decimal("15.00"),
        detected_at=now,
    )
    db.add_all(
        [old_security_finding, current_security_finding, old_cost_finding, current_cost_finding]
    )
    db.flush()

    old_security_recommendation = Recommendation(
        tenant_id=tenant.id,
        cloud_account_id=cloud_account.id,
        finding_id=old_security_finding.id,
        resource_id="bucket-1",
        resource_type="s3_bucket",
        recommendation_type="s3_enable_public_access_block",
        recommendation_category="security",
        summary="Old security recommendation",
        explanation="Old security recommendation",
        risk_level="high",
        recommended_action="Enable public access block.",
        confidence_score="high",
        estimated_savings=None,
        created_at=older,
    )
    current_security_recommendation = Recommendation(
        tenant_id=tenant.id,
        cloud_account_id=cloud_account.id,
        finding_id=current_security_finding.id,
        resource_id="bucket-1",
        resource_type="s3_bucket",
        recommendation_type="s3_enable_public_access_block",
        recommendation_category="security",
        summary="Current security recommendation",
        explanation="Current security recommendation",
        risk_level="high",
        recommended_action="Enable public access block.",
        confidence_score="high",
        estimated_savings=None,
        created_at=now,
    )
    old_cost_recommendation = Recommendation(
        tenant_id=tenant.id,
        cloud_account_id=cloud_account.id,
        finding_id=old_cost_finding.id,
        resource_id="cluster-1",
        resource_type="rds_cluster",
        recommendation_type="aurora_serverless_cost_review",
        recommendation_category="cost",
        summary="Old cost recommendation",
        explanation="Old cost recommendation",
        risk_level="medium",
        recommended_action="Review serverless scaling.",
        confidence_score="medium",
        estimated_savings=Decimal("100.00"),
        created_at=older,
    )
    current_cost_recommendation = Recommendation(
        tenant_id=tenant.id,
        cloud_account_id=cloud_account.id,
        finding_id=current_cost_finding.id,
        resource_id="cluster-1",
        resource_type="rds_cluster",
        recommendation_type="aurora_serverless_cost_review",
        recommendation_category="cost",
        summary="Current cost recommendation",
        explanation="Current cost recommendation",
        risk_level="medium",
        recommended_action="Review serverless scaling.",
        confidence_score="medium",
        estimated_savings=Decimal("15.00"),
        created_at=now,
    )
    db.add_all(
        [
            old_security_recommendation,
            current_security_recommendation,
            old_cost_recommendation,
            current_cost_recommendation,
        ]
    )
    db.commit()

    response = client.get(
        f"/api/v1/tenants/{tenant.id}/cloud-accounts/{cloud_account.id}/summary",
        headers=dev_org_scope["headers"],
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total_recommendations"] == 2
    assert data["total_estimated_monthly_savings"] == 15.0
    assert data["total_cost"] == 100.0
    assert data["savings_percentage"] == 15.0
    assert data["top_cost_services"] == [
        {"service": "Amazon Relational Database Service", "amount": 50.0},
        {"service": "AWS Lambda", "amount": 30.0},
        {"service": "Amazon Simple Storage Service", "amount": 20.0},
    ]
    assert data["by_category"] == {"cost": 1, "security": 1, "governance": 0}
    assert data["by_severity"] == {"high": 1, "medium": 1, "low": 0}
    assert data["top_savings_opportunity"]["summary"] == "Current cost recommendation"
    assert data["top_savings_opportunity"]["estimated_savings"] == 15.0
    assert data["top_risk_issue"]["summary"] == "Current security recommendation"
    assert data["top_risk_issue"]["risk_level"] == "high"
    assert data["cost_period_start"] == "2026-02-23"
    assert data["cost_period_end"] == "2026-03-25"
    assert data["cost_window"] == "rolling_30d"
    assert data["cost_window_label"] == "Rolling last 30 days"
    assert data["cost_metric"] == "UnblendedCost"