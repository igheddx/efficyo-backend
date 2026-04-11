"""Tests for Aurora/RDS DB recommendation quality improvements.

Covers:
  - Aurora Serverless v2 at 0.5 ACU min → no active rightsize recommendation (auto-resolved)
  - Aurora Serverless v2 at 1.0 ACU min → immediate_rightsize recommendation surfaced
  - No scaling config in evidence + low savings → low_value_not_recommended → suppressed
  - No scaling config + high savings → alternative_architecture_review recommendation
  - Alternative architecture recommendation has correct scoring (effort=high, confidence=low, safe_to_apply=False)
  - Immediate rightsize has correct scoring (effort=medium, safe_to_apply=False)
  - Previously active floor-exceeded recommendation is auto-resolved when DB reaches floor
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.cloud_account import CloudAccount
from app.models.finding import Finding
from app.models.recommendation import Recommendation
from app.models.resource_snapshot import ResourceSnapshot
from app.models.tenant import Tenant
from app.services import detection_service, recommendation_service
from app.services.recommendation_service import (
    AURORA_SERVERLESS_V2_FLOOR_ACU,
    AURORA_ALT_ARCH_MIN_MONTHLY_SAVINGS_THRESHOLD,
    DB_CLASSIFICATION_IMMEDIATE_RIGHTSIZE,
    DB_CLASSIFICATION_SERVICE_FLOOR_REACHED,
    DB_CLASSIFICATION_ALT_ARCH_REVIEW,
    DB_CLASSIFICATION_LOW_VALUE,
    _classify_aurora_serverless,
)


# ─────────────────────────── helpers ────────────────────────────────────────

def _setup(db, *, name="db-quality-tenant"):
    tenant = Tenant(name=name, status="active")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    account = CloudAccount(
        tenant_id=tenant.id,
        account_id="999988887777",
        name="db-quality-account",
        status="connected",
        role_arn="arn:aws:iam::999988887777:role/fptnext-validator",
        region_default="us-east-1",
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    return tenant, account


def _aurora_cluster_snapshot(db, tenant, account, *, cluster_id, min_capacity, max_capacity=128.0, engine="aurora-postgresql"):
    """Create an aurora_cluster ResourceSnapshot with serverless_v2_scaling config."""
    config = {
        "engine": engine,
        "engine_mode": "provisioned",
        "serverless_v2_scaling": {
            "MinCapacity": min_capacity,
            "MaxCapacity": max_capacity,
        },
        "publicly_accessible": False,
    }
    snap = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=account.id,
        resource_id=cluster_id,
        resource_type="aurora_cluster",
        region="us-east-1",
        configuration_json=config,
        tags_json={"Name": cluster_id, "Environment": "test"},
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap


def _aurora_instance_snapshot(db, tenant, account, *, instance_id, cluster_id=None, engine="aurora-postgresql"):
    """Create an rds_instance snapshot with db.serverless class (v1-style writer)."""
    config = {
        "db_instance_class": "db.serverless",
        "engine": engine,
        "publicly_accessible": False,
    }
    if cluster_id:
        config["db_cluster_identifier"] = cluster_id
    snap = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=account.id,
        resource_id=instance_id,
        resource_type="rds_instance",
        region="us-east-1",
        configuration_json=config,
        tags_json={"Name": instance_id, "Environment": "test"},
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap


def _all_recs(db, tenant, account):
    return (
        db.query(Recommendation)
        .filter(
            Recommendation.tenant_id == tenant.id,
            Recommendation.cloud_account_id == account.id,
        )
        .all()
    )


def _active_recs(db, tenant, account):
    return [r for r in _all_recs(db, tenant, account) if r.state == "active"]


# ─────────── unit tests for _classify_aurora_serverless ─────────────────────

def test_classify_aurora_at_floor_v2():
    """V2 cluster at 0.5 ACU min → service_floor_reached → suppress=True."""
    evidence = {
        "serverless_v2_scaling": {"MinCapacity": 0.5, "MaxCapacity": 128},
    }
    result = _classify_aurora_serverless(evidence, Decimal("25.50"))
    assert result["classification"] == DB_CLASSIFICATION_SERVICE_FLOOR_REACHED
    assert result["suppress"] is True


def test_classify_aurora_above_floor_v2():
    """V2 cluster at 1.0 ACU min → immediate_rightsize → suppress=False."""
    evidence = {
        "serverless_v2_scaling": {"MinCapacity": 1.0, "MaxCapacity": 128},
    }
    result = _classify_aurora_serverless(evidence, Decimal("43.00"))
    assert result["classification"] == DB_CLASSIFICATION_IMMEDIATE_RIGHTSIZE
    assert result["suppress"] is False
    assert "1.0 ACU" in result["explanation"]
    assert result["safe_to_apply"] is False
    assert result["caution_note"] is not None


def test_classify_aurora_no_scaling_low_savings():
    """No scaling config + savings below threshold → low_value_not_recommended → suppress=True."""
    evidence = {"engine": "aurora-postgresql"}  # no serverless_v2_scaling
    result = _classify_aurora_serverless(evidence, Decimal("25.50"))
    assert result["classification"] == DB_CLASSIFICATION_LOW_VALUE
    assert result["suppress"] is True


def test_classify_aurora_no_scaling_high_savings():
    """No scaling config + savings above threshold → alternative_architecture_review → suppress=False."""
    high_savings = AURORA_ALT_ARCH_MIN_MONTHLY_SAVINGS_THRESHOLD + Decimal("10.00")
    evidence = {"engine": "aurora-postgresql"}
    result = _classify_aurora_serverless(evidence, high_savings)
    assert result["classification"] == DB_CLASSIFICATION_ALT_ARCH_REVIEW
    assert result["suppress"] is False
    assert result["safe_to_apply"] is False
    assert "architecture" in result["caution_note"].lower()


def test_classify_aurora_v1_at_floor():
    """V1 cluster at 1 ACU (v1 floor) → service_floor_reached → suppress=True."""
    evidence = {
        "engine_mode": "serverless",
        "scaling_configuration": {"MinCapacity": 1, "MaxCapacity": 64},
    }
    result = _classify_aurora_serverless(evidence, Decimal("25.00"))
    assert result["classification"] == DB_CLASSIFICATION_SERVICE_FLOOR_REACHED
    assert result["suppress"] is True


# ─────────── integration tests via detection + recommendation pipeline ───────

def test_aurora_at_floor_no_active_recommendation(db):
    """Aurora Serverless v2 at 0.5 ACU min MUST NOT produce an active recommendation."""
    tenant, account = _setup(db, name="floor-tenant")
    _aurora_cluster_snapshot(db, tenant, account, cluster_id="my-cluster", min_capacity=0.5)

    run_id = uuid4()
    detection_service.detect_rds_findings(db, tenant.id, account.id, run_id)
    recommendation_service.generate_rds_recommendations(db, tenant.id, account.id)

    active = _active_recs(db, tenant, account)
    aurora_active = [r for r in active if r.recommendation_type == "aurora_serverless_cost_review"]
    assert aurora_active == [], (
        f"Expected no active Aurora recommendation at service floor, got: {[r.summary for r in aurora_active]}"
    )


def test_aurora_above_floor_produces_immediate_rightsize(db):
    """Aurora Serverless v2 at 2.0 ACU min MUST produce an immediate_rightsize active recommendation."""
    tenant, account = _setup(db, name="abovefloor-tenant")
    _aurora_cluster_snapshot(db, tenant, account, cluster_id="big-cluster", min_capacity=2.0)

    run_id = uuid4()
    detection_service.detect_rds_findings(db, tenant.id, account.id, run_id)
    recommendation_service.generate_rds_recommendations(db, tenant.id, account.id)

    active = _active_recs(db, tenant, account)
    aurora_active = [r for r in active if r.recommendation_type == "aurora_serverless_cost_review"]
    assert len(aurora_active) == 1
    rec = aurora_active[0]
    assert rec.recommendation_classification == DB_CLASSIFICATION_IMMEDIATE_RIGHTSIZE
    assert rec.safe_to_apply is False
    assert rec.caution_note is not None
    assert "2.0 ACU" in (rec.explanation or "") or "2.0 ACU" in (rec.caution_note or "")
    # Effort profile: medium
    assert rec.effort_score == "medium"
    # Wording is config-oriented, not migration-oriented
    assert "minimum capacity" in rec.summary.lower() or "rightsize" in rec.summary.lower() or "reduce" in rec.summary.lower()


def test_alternative_architecture_recommendation_metadata(db):
    """Instance-level db.serverless finding with high enough savings → alt_arch with correct metadata."""
    tenant, account = _setup(db, name="altarch-tenant")
    # Create a db.serverless instance without a cluster snapshot → no scaling config in evidence
    _aurora_instance_snapshot(db, tenant, account, instance_id="orphan-writer")

    # Patch pricing to return savings above threshold for this test
    from app.services import pricing_service as ps
    original = ps.AURORA_SERVERLESS_MONTHLY_PRICE_ESTIMATES.copy()
    ps.AURORA_SERVERLESS_MONTHLY_PRICE_ESTIMATES["aurora-postgresql"] = Decimal("250.00")

    try:
        run_id = uuid4()
        detection_service.detect_rds_findings(db, tenant.id, account.id, run_id)
        recommendation_service.generate_rds_recommendations(db, tenant.id, account.id)

        active = _active_recs(db, tenant, account)
        aurora_active = [r for r in active if r.recommendation_type == "aurora_serverless_cost_review"]
        assert len(aurora_active) == 1
        rec = aurora_active[0]
        assert rec.recommendation_classification == DB_CLASSIFICATION_ALT_ARCH_REVIEW
        assert rec.safe_to_apply is False
        assert rec.effort_score == "high"
        assert rec.confidence_score == "low"
        assert rec.actionability_type == "review_required"
        assert "architecture" in rec.summary.lower() or "migration" in rec.summary.lower() or "review" in rec.summary.lower()
    finally:
        ps.AURORA_SERVERLESS_MONTHLY_PRICE_ESTIMATES.update(original)


def test_floor_reached_auto_resolves_existing_recommendation(db):
    """An existing active Aurora recommendation is auto-resolved when the cluster reaches service floor."""
    tenant, account = _setup(db, name="autoresolve-tenant")

    # First sync: cluster at 2.0 ACU (above floor) → produces active recommendation
    _aurora_cluster_snapshot(db, tenant, account, cluster_id="resolving-cluster", min_capacity=2.0)
    run1 = uuid4()
    detection_service.detect_rds_findings(db, tenant.id, account.id, run1)
    recommendation_service.generate_rds_recommendations(db, tenant.id, account.id)

    active_before = _active_recs(db, tenant, account)
    assert any(r.recommendation_type == "aurora_serverless_cost_review" for r in active_before), \
        "Expected active recommendation before reaching floor"

    # Second sync: cluster now at 0.5 ACU (floor) — simulate by replacing the snapshot
    from app.models.resource_snapshot import ResourceSnapshot as RS
    db.query(RS).filter(RS.cloud_account_id == account.id).delete()
    db.commit()

    _aurora_cluster_snapshot(db, tenant, account, cluster_id="resolving-cluster", min_capacity=0.5)
    run2 = uuid4()
    detection_service.detect_rds_findings(db, tenant.id, account.id, run2)
    recommendation_service.generate_rds_recommendations(db, tenant.id, account.id)

    active_after = _active_recs(db, tenant, account)
    aurora_active_after = [r for r in active_after if r.recommendation_type == "aurora_serverless_cost_review"]
    assert aurora_active_after == [], (
        "Expected Aurora recommendation to be auto-resolved when cluster is at service floor"
    )

    # Confirm resolved state exists
    all_after = _all_recs(db, tenant, account)
    aurora_resolved = [r for r in all_after if r.recommendation_type == "aurora_serverless_cost_review" and r.state == "resolved"]
    assert aurora_resolved, "Expected at least one resolved Aurora recommendation after reaching floor"
