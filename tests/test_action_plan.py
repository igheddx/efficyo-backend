from datetime import timedelta
from decimal import Decimal

from app.core.db import utc_now
from app.models.cloud_account import CloudAccount
from app.models.finding import Finding
from app.models.recommendation import Recommendation
from app.models.recommendation_outcome import RecommendationOutcome
from app.models.resource_snapshot import ResourceSnapshot
from app.models.tenant import Tenant
from app.services import recommendation_service


def test_action_plan_excludes_applied_and_orders(db):
    tenant = Tenant(name="plan-tenant")
    db.add(tenant)
    db.flush()

    cloud = CloudAccount(
        tenant_id=tenant.id,
        account_id="123456789012",
        name="plan-cloud",
        role_arn="arn:aws:iam::123456789012:role/OptimizationRole",
        region_default="us-east-1",
    )
    db.add(cloud)
    db.flush()

    now = utc_now()
    s1 = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=cloud.id,
        resource_id="r1",
        resource_type="s3_bucket",
        region="us-east-1",
        configuration_json={},
        tags_json={},
        captured_at=now,
    )
    s2 = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=cloud.id,
        resource_id="r2",
        resource_type="rds_instance",
        region="us-east-1",
        configuration_json={},
        tags_json={},
        captured_at=now,
    )
    db.add_all([s1, s2])
    db.flush()

    f1 = Finding(
        tenant_id=tenant.id,
        cloud_account_id=cloud.id,
        resource_snapshot_id=s1.id,
        resource_id="r1",
        resource_type="s3_bucket",
        finding_type="s3_public_access_candidate",
        severity="high",
        evidence_json={"current_monthly_cost": 12.0},
        detected_at=now,
    )
    f2 = Finding(
        tenant_id=tenant.id,
        cloud_account_id=cloud.id,
        resource_snapshot_id=s2.id,
        resource_id="r2",
        resource_type="rds_instance",
        finding_type="aurora_serverless_review_candidate",
        severity="medium",
        evidence_json={"current_monthly_cost": 30.0},
        detected_at=now,
    )
    db.add_all([f1, f2])
    db.flush()

    r1 = Recommendation(
        tenant_id=tenant.id,
        cloud_account_id=cloud.id,
        finding_id=f1.id,
        resource_id="r1",
        resource_type="s3_bucket",
        recommendation_type="s3_enable_public_access_block",
        recommendation_category="security",
        summary="Enable S3 Public Access Block",
        explanation="x",
        risk_level="high",
        recommended_action="x",
        confidence_score="high",
        estimated_savings=Decimal("5.00"),
        created_at=now - timedelta(minutes=1),
    )
    r2 = Recommendation(
        tenant_id=tenant.id,
        cloud_account_id=cloud.id,
        finding_id=f2.id,
        resource_id="r2",
        resource_type="rds_instance",
        recommendation_type="aurora_serverless_cost_review",
        recommendation_category="cost",
        summary="Review Aurora Serverless configuration for cost efficiency",
        explanation="x",
        risk_level="medium",
        recommended_action="x",
        confidence_score="medium",
        estimated_savings=Decimal("25.00"),
        created_at=now,
    )
    db.add_all([r1, r2])
    db.flush()

    out = RecommendationOutcome(
        tenant_id=tenant.id,
        cloud_account_id=cloud.id,
        recommendation_id=r1.id,
        resource_id=r1.resource_id,
        recommendation_type=r1.recommendation_type,
        recommendation_category=r1.recommendation_category,
        status="acted_on",
        workflow_status="applied",
    )
    db.add(out)
    db.commit()

    items = recommendation_service.get_action_plan(db, tenant.id, cloud.id, limit=3)
    assert len(items) == 1
    assert items[0]["recommendation_id"] == r2.id
    assert items[0]["step_number"] == 1


def test_action_plan_dedupes_aurora_serverless_same_cluster(db):
    """Instance + cluster Aurora Serverless recs collapse to one Recommended Plan step."""
    tenant = Tenant(name="plan-aurora-dedupe")
    db.add(tenant)
    db.flush()

    cloud = CloudAccount(
        tenant_id=tenant.id,
        account_id="123456789012",
        name="plan-cloud",
        role_arn="arn:aws:iam::123456789012:role/OptimizationRole",
        region_default="us-east-1",
    )
    db.add(cloud)
    db.flush()

    now = utc_now()
    s_inst = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=cloud.id,
        resource_id="appdb-instance-1",
        resource_type="rds_instance",
        region="us-east-1",
        configuration_json={},
        tags_json={},
        captured_at=now,
    )
    s_cluster = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=cloud.id,
        resource_id="appdb",
        resource_type="aurora_cluster",
        region="us-east-1",
        configuration_json={},
        tags_json={},
        captured_at=now,
    )
    db.add_all([s_inst, s_cluster])
    db.flush()

    f_inst = Finding(
        tenant_id=tenant.id,
        cloud_account_id=cloud.id,
        resource_snapshot_id=s_inst.id,
        resource_id="appdb-instance-1",
        resource_type="rds_instance",
        finding_type="aurora_serverless_review_candidate",
        severity="medium",
        evidence_json={"current_monthly_cost": 20.0},
        detected_at=now,
    )
    f_cluster = Finding(
        tenant_id=tenant.id,
        cloud_account_id=cloud.id,
        resource_snapshot_id=s_cluster.id,
        resource_id="appdb",
        resource_type="aurora_cluster",
        finding_type="aurora_serverless_review_candidate",
        severity="medium",
        evidence_json={"current_monthly_cost": 25.0},
        detected_at=now,
    )
    db.add_all([f_inst, f_cluster])
    db.flush()

    r_inst = Recommendation(
        tenant_id=tenant.id,
        cloud_account_id=cloud.id,
        finding_id=f_inst.id,
        resource_id="appdb-instance-1",
        resource_type="rds_instance",
        recommendation_type="aurora_serverless_cost_review",
        recommendation_category="cost",
        summary="Review Aurora Serverless configuration for cost efficiency",
        explanation="x",
        risk_level="medium",
        recommended_action="x",
        confidence_score="medium",
        estimated_savings=Decimal("20.00"),
        created_at=now,
    )
    r_cluster = Recommendation(
        tenant_id=tenant.id,
        cloud_account_id=cloud.id,
        finding_id=f_cluster.id,
        resource_id="appdb",
        resource_type="aurora_cluster",
        recommendation_type="aurora_serverless_cost_review",
        recommendation_category="cost",
        summary="Review Aurora Serverless configuration for cost efficiency",
        explanation="x",
        risk_level="medium",
        recommended_action="x",
        confidence_score="medium",
        estimated_savings=Decimal("25.00"),
        created_at=now,
    )
    db.add_all([r_inst, r_cluster])
    db.commit()

    items = recommendation_service.get_action_plan(db, tenant.id, cloud.id, limit=5)
    aurora_steps = [i for i in items if "Aurora Serverless" in (i.get("summary") or "")]
    assert len(aurora_steps) == 1
    assert aurora_steps[0]["recommendation_id"] == r_cluster.id

    listed = recommendation_service.list_recommendations(db, tenant.id, cloud.id, latest_only=True)
    aurora_recs = [r for r in listed if r.recommendation_type == "aurora_serverless_cost_review"]
    assert len(aurora_recs) == 1
    assert aurora_recs[0].id == r_cluster.id

    tops = recommendation_service.get_top_opportunities(db, tenant.id, cloud.id, limit=10)
    aurora_tops = [t for t in tops if t.recommendation_type == "aurora_serverless_cost_review"]
    assert len(aurora_tops) == 1
    assert aurora_tops[0].recommendation_id == r_cluster.id

