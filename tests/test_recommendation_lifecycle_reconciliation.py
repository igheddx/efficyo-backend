from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.models.cloud_account import CloudAccount
from app.models.finding import Finding
from app.models.organization import Organization
from app.models.recommendation import Recommendation
from app.models.resource_snapshot import ResourceSnapshot
from app.models.tenant import Tenant
from app.services import recommendation_service


def _seed_scope(db):
    org = Organization(name="rec-org", slug=f"rec-org-{uuid4().hex[:8]}")
    db.add(org)
    db.commit()
    db.refresh(org)

    tenant = Tenant(name="rec-tenant", status="active", organization_id=org.id)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    account = CloudAccount(
        tenant_id=tenant.id,
        account_id="123456789012",
        name="rec-account",
        status="connected",
        role_arn="arn:aws:iam::123456789012:role/fptnext-validator",
        region_default="us-east-1",
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return tenant, account


def _seed_snapshot(db, tenant_id, account_id, resource_id="fn-a"):
    snap = ResourceSnapshot(
        tenant_id=tenant_id,
        cloud_account_id=account_id,
        resource_id=resource_id,
        resource_type="lambda_function",
        region="us-east-1",
        configuration_json={"runtime": "python3.11"},
        tags_json={"Name": "fn-a", "Environment": "dev"},
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap


def _seed_finding(db, *, tenant_id, account_id, snapshot_id, resource_id, sync_run_id, finding_type="lambda_outdated_runtime"):
    f = Finding(
        tenant_id=tenant_id,
        cloud_account_id=account_id,
        resource_snapshot_id=snapshot_id,
        resource_id=resource_id,
        resource_type="lambda_function",
        finding_type=finding_type,
        severity="medium",
        evidence_json={"runtime": "python3.6"},
        detected_at=datetime.now(timezone.utc),
        sync_run_id=sync_run_id,
    )
    db.add(f)
    db.commit()
    return f


def test_recommendation_resolves_when_condition_not_true_next_ingest(db):
    tenant, account = _seed_scope(db)
    snap = _seed_snapshot(db, tenant.id, account.id)

    run1 = uuid4()
    _seed_finding(
        db,
        tenant_id=tenant.id,
        account_id=account.id,
        snapshot_id=snap.id,
        resource_id=snap.resource_id,
        sync_run_id=run1,
    )
    recommendation_service.generate_rds_recommendations(db, tenant.id, account.id, sync_run_id=run1)

    rec = db.query(Recommendation).filter(Recommendation.tenant_id == tenant.id).one()
    assert rec.state == "active"

    # Next ingest has no matching finding for this key -> auto-resolved.
    run2 = uuid4()
    recommendation_service.generate_rds_recommendations(db, tenant.id, account.id, sync_run_id=run2)

    db.refresh(rec)
    assert rec.state == "resolved"
    assert rec.resolution_source == "auto"
    assert rec.resolved_at is not None


def test_dismissed_recommendation_not_reopened_by_reingest(db):
    tenant, account = _seed_scope(db)
    snap = _seed_snapshot(db, tenant.id, account.id)

    run1 = uuid4()
    _seed_finding(
        db,
        tenant_id=tenant.id,
        account_id=account.id,
        snapshot_id=snap.id,
        resource_id=snap.resource_id,
        sync_run_id=run1,
    )
    recommendation_service.generate_rds_recommendations(db, tenant.id, account.id, sync_run_id=run1)

    rec = db.query(Recommendation).filter(Recommendation.tenant_id == tenant.id).one()
    recommendation_service.dismiss_recommendation(
        db,
        tenant.id,
        account.id,
        rec.id,
        actor="user@example.com",
        reason="not_applicable",
        note="Intentionally ignored",
    )

    run2 = uuid4()
    _seed_finding(
        db,
        tenant_id=tenant.id,
        account_id=account.id,
        snapshot_id=snap.id,
        resource_id=snap.resource_id,
        sync_run_id=run2,
    )
    recommendation_service.generate_rds_recommendations(db, tenant.id, account.id, sync_run_id=run2)

    rows = db.query(Recommendation).filter(Recommendation.tenant_id == tenant.id).all()
    assert len(rows) == 1
    assert rows[0].state == "dismissed"


def test_no_duplicate_recommendations_for_same_stable_key(db):
    tenant, account = _seed_scope(db)
    snap = _seed_snapshot(db, tenant.id, account.id)

    run1 = uuid4()
    _seed_finding(
        db,
        tenant_id=tenant.id,
        account_id=account.id,
        snapshot_id=snap.id,
        resource_id=snap.resource_id,
        sync_run_id=run1,
    )
    recommendation_service.generate_rds_recommendations(db, tenant.id, account.id, sync_run_id=run1)

    run2 = uuid4()
    _seed_finding(
        db,
        tenant_id=tenant.id,
        account_id=account.id,
        snapshot_id=snap.id,
        resource_id=snap.resource_id,
        sync_run_id=run2,
    )
    recommendation_service.generate_rds_recommendations(db, tenant.id, account.id, sync_run_id=run2)

    rows = db.query(Recommendation).filter(Recommendation.tenant_id == tenant.id).all()
    assert len(rows) == 1


def test_snoozed_recommendation_becomes_resolved_when_issue_disappears(db):
    tenant, account = _seed_scope(db)
    snap = _seed_snapshot(db, tenant.id, account.id)

    run1 = uuid4()
    _seed_finding(
        db,
        tenant_id=tenant.id,
        account_id=account.id,
        snapshot_id=snap.id,
        resource_id=snap.resource_id,
        sync_run_id=run1,
    )
    recommendation_service.generate_rds_recommendations(db, tenant.id, account.id, sync_run_id=run1)

    rec = db.query(Recommendation).filter(Recommendation.tenant_id == tenant.id).one()
    recommendation_service.snooze_recommendation(
        db,
        tenant.id,
        account.id,
        rec.id,
        actor="user@example.com",
        days=30,
    )

    run2 = uuid4()
    recommendation_service.generate_rds_recommendations(db, tenant.id, account.id, sync_run_id=run2)

    db.refresh(rec)
    assert rec.state == "resolved"
    assert rec.resolution_source == "auto"


def test_expired_snooze_returns_to_active_when_issue_still_present(db):
    tenant, account = _seed_scope(db)
    snap = _seed_snapshot(db, tenant.id, account.id)

    run1 = uuid4()
    _seed_finding(
        db,
        tenant_id=tenant.id,
        account_id=account.id,
        snapshot_id=snap.id,
        resource_id=snap.resource_id,
        sync_run_id=run1,
    )
    recommendation_service.generate_rds_recommendations(db, tenant.id, account.id, sync_run_id=run1)

    rec = db.query(Recommendation).filter(Recommendation.tenant_id == tenant.id).one()
    rec.state = "snoozed"
    rec.snoozed_until = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()

    run2 = uuid4()
    _seed_finding(
        db,
        tenant_id=tenant.id,
        account_id=account.id,
        snapshot_id=snap.id,
        resource_id=snap.resource_id,
        sync_run_id=run2,
    )
    recommendation_service.generate_rds_recommendations(db, tenant.id, account.id, sync_run_id=run2)

    db.refresh(rec)
    assert rec.state == "active"
    assert rec.snoozed_until is None
