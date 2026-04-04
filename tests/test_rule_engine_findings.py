"""Unit tests for build_finding_from_rule (no database required)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.rules.finding_factory import build_finding_from_rule
from app.rules.registry import ComputedEvidenceField, RuleCondition, RuleDefinition


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_snapshot(
    *,
    resource_type: str,
    resource_id: str = "res-001",
    configuration_json: dict | None = None,
    tags_json: dict | None = None,
) -> SimpleNamespace:
    snap = SimpleNamespace()
    snap.id = uuid4()
    snap.tenant_id = uuid4()
    snap.cloud_account_id = uuid4()
    snap.resource_id = resource_id
    snap.resource_type = resource_type
    snap.configuration_json = configuration_json or {}
    snap.tags_json = tags_json or {}
    return snap


def _make_rule(**overrides) -> RuleDefinition:
    defaults = dict(
        rule_id="test_rule",
        enabled=True,
        resource_type="cloudfront_distribution",
        finding_type="cloudfront_test_finding",
        recommendation_type="cloudfront_review_test",
        category="security",
        severity="high",
        impact="high",
        effort="low",
        confidence="high",
        actionability="review_required",
        title="Test Finding",
        summary_template="Distribution {resource_id} has an issue.",
        why_this_matters="It matters.",
        guided_action_key="cloudfront_review_protocol",
        recommended_action="Review protocol policy.",
        approval_required=False,
        execution_eligible=False,
        conditions=[],
        evidence_fields=[],
        evidence_computed=[],
        tags=[],
    )
    defaults.update(overrides)
    return RuleDefinition(**defaults)


# ── Basic finding structure ───────────────────────────────────────────────────

def test_finding_has_correct_finding_type():
    rule = _make_rule(finding_type="cloudfront_insecure_viewer_protocol_policy")
    snap = _make_snapshot(resource_type="cloudfront_distribution")
    tenant_id = uuid4()
    f = build_finding_from_rule(rule, snap, datetime.now(timezone.utc), tenant_id, snap.cloud_account_id, uuid4())
    assert f.finding_type == "cloudfront_insecure_viewer_protocol_policy"


def test_finding_has_correct_severity():
    rule = _make_rule(severity="high")
    snap = _make_snapshot(resource_type="cloudfront_distribution")
    f = build_finding_from_rule(rule, snap, datetime.now(timezone.utc), uuid4(), uuid4(), uuid4())
    assert f.severity == "high"


def test_finding_has_correct_resource_type():
    rule = _make_rule(resource_type="lambda_function")
    snap = _make_snapshot(resource_type="lambda_function")
    f = build_finding_from_rule(rule, snap, datetime.now(timezone.utc), uuid4(), uuid4(), uuid4())
    assert f.resource_type == "lambda_function"


def test_finding_links_to_snapshot():
    rule = _make_rule()
    snap = _make_snapshot(resource_type="cloudfront_distribution")
    f = build_finding_from_rule(rule, snap, datetime.now(timezone.utc), uuid4(), uuid4(), uuid4())
    assert f.resource_snapshot_id == snap.id


def test_finding_evidence_json_has_title():
    rule = _make_rule(title="Test Finding Title")
    snap = _make_snapshot(resource_type="cloudfront_distribution")
    f = build_finding_from_rule(rule, snap, datetime.now(timezone.utc), uuid4(), uuid4(), uuid4())
    assert f.evidence_json is not None
    assert f.evidence_json.get("title") == "Test Finding Title"


def test_finding_summary_includes_resource_id():
    rule = _make_rule(summary_template="Resource {resource_id} has a problem.")
    snap = _make_snapshot(resource_type="cloudfront_distribution", resource_id="cf-abc-123")
    f = build_finding_from_rule(rule, snap, datetime.now(timezone.utc), uuid4(), uuid4(), uuid4())
    assert "cf-abc-123" in f.evidence_json.get("summary", "")


# ── Evidence fields ───────────────────────────────────────────────────────────

def test_evidence_fields_copied_from_cfg():
    rule = _make_rule(evidence_fields=["viewer_protocol_policy", "distribution_id"])
    snap = _make_snapshot(
        resource_type="cloudfront_distribution",
        configuration_json={
            "viewer_protocol_policy": "allow-all",
            "distribution_id": "E1ABCXYZ",
        },
    )
    f = build_finding_from_rule(rule, snap, datetime.now(timezone.utc), uuid4(), uuid4(), uuid4())
    ev = f.evidence_json or {}
    nested = ev.get("evidence", {})
    assert nested.get("viewer_protocol_policy") == "allow-all"
    assert nested.get("distribution_id") == "E1ABCXYZ"


def test_evidence_fields_missing_keys_are_skipped():
    rule = _make_rule(evidence_fields=["viewer_protocol_policy", "absent_field"])
    snap = _make_snapshot(
        resource_type="cloudfront_distribution",
        configuration_json={"viewer_protocol_policy": "allow-all"},
    )
    f = build_finding_from_rule(rule, snap, datetime.now(timezone.utc), uuid4(), uuid4(), uuid4())
    nested = (f.evidence_json or {}).get("evidence", {})
    assert "viewer_protocol_policy" in nested
    assert "absent_field" not in nested


# ── Tagging rule evidence ─────────────────────────────────────────────────────

def test_tagging_rule_missing_tags_injected():
    rule = _make_rule(
        finding_type="cloudfront_distribution_missing_required_tags",
        conditions=[RuleCondition(op="any_tag_missing", tags=["Name", "Environment"])],
    )
    snap = _make_snapshot(
        resource_type="cloudfront_distribution",
        tags_json={"Name": "my-cf"},  # Environment missing
    )
    f = build_finding_from_rule(rule, snap, datetime.now(timezone.utc), uuid4(), uuid4(), uuid4())
    nested = (f.evidence_json or {}).get("evidence", {})
    assert nested.get("missing_tags") == ["Environment"]


def test_tagging_rule_tags_dict_included_in_evidence():
    rule = _make_rule(
        finding_type="cloudfront_distribution_missing_required_tags",
        conditions=[RuleCondition(op="any_tag_missing", tags=["Name", "Environment"])],
    )
    snap = _make_snapshot(
        resource_type="cloudfront_distribution",
        tags_json={"Name": "my-cf"},
    )
    f = build_finding_from_rule(rule, snap, datetime.now(timezone.utc), uuid4(), uuid4(), uuid4())
    nested = (f.evidence_json or {}).get("evidence", {})
    assert "tags" in nested
    assert nested["tags"].get("Name") == "my-cf"


# ── ACM computed evidence ─────────────────────────────────────────────────────

def test_evidence_computed_days_remaining():
    future_date = (datetime.now(timezone.utc) + timedelta(days=20)).isoformat()
    rule = _make_rule(
        finding_type="acm_certificate_expiring_soon",
        evidence_computed=[ComputedEvidenceField(key="days_remaining", op="days_until", source_field="not_after")],
    )
    snap = _make_snapshot(resource_type="acm_certificate", configuration_json={"not_after": future_date})
    f = build_finding_from_rule(rule, snap, datetime.now(timezone.utc), uuid4(), uuid4(), uuid4())
    nested = (f.evidence_json or {}).get("evidence", {})
    days = nested.get("days_remaining")
    assert days is not None
    assert 18 <= days <= 21


def test_evidence_computed_missing_source_field_is_skipped():
    rule = _make_rule(
        evidence_computed=[ComputedEvidenceField(key="days_remaining", op="days_until", source_field="not_after")],
    )
    snap = _make_snapshot(resource_type="acm_certificate", configuration_json={})  # no not_after
    f = build_finding_from_rule(rule, snap, datetime.now(timezone.utc), uuid4(), uuid4(), uuid4())
    nested = (f.evidence_json or {}).get("evidence", {})
    assert "days_remaining" not in nested


def test_evidence_computed_invalid_date_is_skipped():
    rule = _make_rule(
        evidence_computed=[ComputedEvidenceField(key="days_remaining", op="days_until", source_field="not_after")],
    )
    snap = _make_snapshot(resource_type="acm_certificate", configuration_json={"not_after": "not-a-date"})
    f = build_finding_from_rule(rule, snap, datetime.now(timezone.utc), uuid4(), uuid4(), uuid4())
    nested = (f.evidence_json or {}).get("evidence", {})
    assert "days_remaining" not in nested


# ── IDs are threaded correctly ────────────────────────────────────────────────

def test_tenant_and_cloud_account_ids_are_set():
    rule = _make_rule()
    snap = _make_snapshot(resource_type="cloudfront_distribution")
    tenant_id = uuid4()
    cloud_account_id = uuid4()
    sync_run_id = uuid4()
    f = build_finding_from_rule(rule, snap, datetime.now(timezone.utc), tenant_id, cloud_account_id, sync_run_id)
    assert f.tenant_id == tenant_id
    assert f.cloud_account_id == cloud_account_id
    assert f.sync_run_id == sync_run_id
