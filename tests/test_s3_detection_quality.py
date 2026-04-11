"""Tests for S3 detection quality fixes and recommendation safety metadata.

Covers:
  - S3 encryption: AES256 → no finding
  - S3 encryption: aws:kms → no finding
  - S3 encryption: confirmed absent → finding emitted
  - S3 encryption: unknown (None) → no finding (safe default)
  - S3 public access block → review_required recommendation with safe_to_apply=False
  - Recommendation wording reflects confidence and actionability
  - False-positive S3 encryption recommendations auto-resolve on re-ingest
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.models.cloud_account import CloudAccount
from app.models.finding import Finding
from app.models.recommendation import Recommendation
from app.models.resource_snapshot import ResourceSnapshot
from app.models.tenant import Tenant
from app.services import detection_service, recommendation_service


# ─────────────────────────── helpers ───────────────────────────────────────

def _setup(db):
    tenant = Tenant(name="s3-quality-tenant", status="active")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    account = CloudAccount(
        tenant_id=tenant.id,
        account_id="111122223333",
        name="s3-quality-account",
        status="connected",
        role_arn="arn:aws:iam::111122223333:role/fptnext-validator",
        region_default="us-east-1",
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    return tenant, account


def _s3_snapshot(db, tenant, account, *, name, encryption_enabled, pab=None):
    """Create an S3 ResourceSnapshot with the given configuration fields."""
    pab = pab or {
        "block_public_acls": True,
        "ignore_public_acls": True,
        "block_public_policy": True,
        "restrict_public_buckets": True,
    }
    snap = ResourceSnapshot(
        tenant_id=tenant.id,
        cloud_account_id=account.id,
        resource_id=name,
        resource_type="s3_bucket",
        region="us-east-1",
        configuration_json={
            "versioning_status": "Enabled",
            "encryption_enabled": encryption_enabled,
            "public_access_block_status": pab,
            "lifecycle_rules_count": 1,
            "bucket_size_bytes_approx": None,
        },
        tags_json={"Name": name, "Environment": "test"},
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap


def _finding_types(db, tenant, account):
    return {
        f.finding_type
        for f in db.query(Finding)
        .filter(Finding.tenant_id == tenant.id, Finding.cloud_account_id == account.id)
        .all()
    }


def _recommendations(db, tenant, account):
    return (
        db.query(Recommendation)
        .filter(
            Recommendation.tenant_id == tenant.id,
            Recommendation.cloud_account_id == account.id,
        )
        .all()
    )


# ─────────────────────────── encryption tests ─────────────────────────────


def test_s3_aes256_encryption_no_finding(db):
    """Bucket with encryption_enabled=True (AES256 stored state) must NOT produce s3_missing_encryption."""
    tenant, account = _setup(db)
    _s3_snapshot(db, tenant, account, name="aes256-bucket", encryption_enabled=True)

    run_id = uuid4()
    detection_service.detect_s3_findings(db, tenant.id, account.id, run_id)

    assert "s3_missing_encryption" not in _finding_types(db, tenant, account)


def test_s3_kms_encryption_no_finding(db):
    """Bucket where inventory resolved encryption_enabled=True (aws:kms) must NOT produce s3_missing_encryption."""
    tenant, account = _setup(db)
    # In the normalized snapshot, the value is a boolean; True means valid algorithm was found.
    _s3_snapshot(db, tenant, account, name="kms-bucket", encryption_enabled=True)

    run_id = uuid4()
    detection_service.detect_s3_findings(db, tenant.id, account.id, run_id)

    assert "s3_missing_encryption" not in _finding_types(db, tenant, account)


def test_s3_missing_encryption_emits_finding(db):
    """Bucket with confirmed encryption_enabled=False MUST produce s3_missing_encryption."""
    tenant, account = _setup(db)
    _s3_snapshot(db, tenant, account, name="unencrypted-bucket", encryption_enabled=False)

    run_id = uuid4()
    detection_service.detect_s3_findings(db, tenant.id, account.id, run_id)

    assert "s3_missing_encryption" in _finding_types(db, tenant, account)


def test_s3_encryption_unknown_no_finding(db):
    """Bucket with encryption_enabled=None (API undetermined) must NOT produce s3_missing_encryption.

    This prevents false positives from permission errors or throttling during ingest.
    """
    tenant, account = _setup(db)
    _s3_snapshot(db, tenant, account, name="unknown-enc-bucket", encryption_enabled=None)

    run_id = uuid4()
    detection_service.detect_s3_findings(db, tenant.id, account.id, run_id)

    assert "s3_missing_encryption" not in _finding_types(db, tenant, account)


# ─────────────────────── public access tests ──────────────────────────────


def test_s3_public_access_block_off_produces_review_required_recommendation(db):
    """Bucket without full PAB must produce a review_required recommendation with safe_to_apply=False."""
    tenant, account = _setup(db)
    _s3_snapshot(
        db,
        tenant,
        account,
        name="public-bucket",
        encryption_enabled=True,
        pab={
            "block_public_acls": False,
            "ignore_public_acls": False,
            "block_public_policy": False,
            "restrict_public_buckets": False,
        },
    )

    run_id = uuid4()
    detection_service.detect_s3_findings(db, tenant.id, account.id, run_id)
    recommendation_service.generate_rds_recommendations(db, tenant.id, account.id, sync_run_id=run_id)

    recs = _recommendations(db, tenant, account)
    pab_recs = [r for r in recs if r.recommendation_type == "s3_enable_public_access_block"]
    assert len(pab_recs) == 1, "Expected exactly one s3_enable_public_access_block recommendation"

    rec = pab_recs[0]
    assert rec.actionability_type == "review_required", (
        f"Expected review_required, got {rec.actionability_type}"
    )
    assert rec.safe_to_apply is False, "S3 public access recommendation must have safe_to_apply=False"
    assert rec.caution_note is not None and len(rec.caution_note) > 0, "caution_note must be set"
    assert rec.confidence_score == "medium", (
        f"Expected medium confidence for PAB recommendation, got {rec.confidence_score}"
    )


def test_s3_public_access_recommendation_wording(db):
    """Summary should say 'Review S3 public access posture', not 'Enable S3 Public Access Block'."""
    tenant, account = _setup(db)
    _s3_snapshot(
        db,
        tenant,
        account,
        name="public-wording-bucket",
        encryption_enabled=True,
        pab={
            "block_public_acls": False,
            "ignore_public_acls": True,
            "block_public_policy": False,
            "restrict_public_buckets": True,
        },
    )

    run_id = uuid4()
    detection_service.detect_s3_findings(db, tenant.id, account.id, run_id)
    recommendation_service.generate_rds_recommendations(db, tenant.id, account.id, sync_run_id=run_id)

    recs = _recommendations(db, tenant, account)
    pab_recs = [r for r in recs if r.recommendation_type == "s3_enable_public_access_block"]
    assert pab_recs, "Expected s3_enable_public_access_block recommendation"

    rec = pab_recs[0]
    assert "review" in rec.summary.lower(), (
        f"Expected review-oriented summary, got: {rec.summary!r}"
    )
    assert "enable s3 public access block" not in rec.summary.lower(), (
        "Summary must not be the old imperative wording"
    )


# ─────────────────── false-positive resolution on re-ingest ───────────────


def test_false_positive_encryption_recommendation_resolves_on_reingest(db):
    """After encryption is confirmed present, a previously created false-positive recommendation auto-resolves."""
    tenant, account = _setup(db)

    # Simulate first ingest: encryption_enabled=False (false positive from old bug)
    snap = _s3_snapshot(db, tenant, account, name="fp-bucket", encryption_enabled=False)

    run_id_1 = uuid4()
    detection_service.detect_s3_findings(db, tenant.id, account.id, run_id_1)
    recommendation_service.generate_rds_recommendations(db, tenant.id, account.id, sync_run_id=run_id_1)

    recs_before = _recommendations(db, tenant, account)
    enc_recs = [r for r in recs_before if r.recommendation_type == "enable_encryption"]
    assert len(enc_recs) == 1, "Expected one enable_encryption recommendation after first ingest"
    assert enc_recs[0].state == "active"

    # Simulate second ingest: bucket actually has encryption (fixed snapshot)
    snap.configuration_json = {
        **snap.configuration_json,
        "encryption_enabled": True,
    }
    db.commit()

    run_id_2 = uuid4()
    detection_service.detect_s3_findings(db, tenant.id, account.id, run_id_2)
    recommendation_service.generate_rds_recommendations(db, tenant.id, account.id, sync_run_id=run_id_2)

    # The old recommendation should now be resolved
    db.refresh(enc_recs[0])
    assert enc_recs[0].state == "resolved", (
        f"Expected recommendation to be resolved after fix, got state={enc_recs[0].state!r}"
    )
    assert enc_recs[0].resolution_source == "auto"
