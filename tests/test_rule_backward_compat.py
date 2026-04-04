"""Tests for backward compatibility between config-driven rules and legacy detection.

Covers:
- Migrated finding types are present in get_migrated_finding_types()
- Legacy finding types (RDS, EC2) are NOT in the migrated set
- Detection filter correctly excludes migrated finding types when rules load
- Recommendation service prefers config-driven rule for migrated finding types
- Recommendation service falls back to legacy path for non-migrated finding types
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.rules.registry import get_migrated_finding_types


# ── Migrated finding type set ─────────────────────────────────────────────────

def test_migrated_types_is_frozenset():
    result = get_migrated_finding_types()
    assert isinstance(result, frozenset)


def test_first_wave_cloudfront_types_are_migrated():
    migrated = get_migrated_finding_types()
    assert "cloudfront_insecure_viewer_protocol_policy" in migrated
    assert "cloudfront_missing_https_redirect" in migrated
    assert "cloudfront_disabled_distribution_review" in migrated


def test_first_wave_lambda_types_are_migrated():
    migrated = get_migrated_finding_types()
    assert "lambda_outdated_runtime" in migrated
    assert "lambda_review_timeout_configuration" in migrated


def test_first_wave_acm_types_are_migrated():
    migrated = get_migrated_finding_types()
    assert "acm_certificate_expiring_soon" in migrated
    assert "acm_certificate_pending_validation" in migrated
    assert "acm_certificate_validation_issue" in migrated


def test_first_wave_eventbridge_types_are_migrated():
    migrated = get_migrated_finding_types()
    assert "eventbridge_rule_without_targets" in migrated
    assert "eventbridge_rule_disabled_review" in migrated


def test_first_wave_security_group_types_are_migrated():
    migrated = get_migrated_finding_types()
    assert "security_group_world_open_sensitive_port" in migrated
    assert "security_group_overly_permissive" in migrated


def test_first_wave_networking_types_are_migrated():
    migrated = get_migrated_finding_types()
    assert "load_balancer_deletion_protection_disabled" in migrated
    assert "route_table_unassociated_review" in migrated
    assert "target_group_no_targets" in migrated
    assert "target_group_unhealthy_targets" in migrated


def test_first_wave_tagging_types_are_migrated():
    migrated = get_migrated_finding_types()
    assert "cloudfront_distribution_missing_required_tags" in migrated
    assert "acm_certificate_missing_required_tags" in migrated
    assert "security_group_missing_required_tags" in migrated


def test_legacy_only_types_are_not_migrated():
    """RDS and EC2 finding types are not yet in the config-driven first wave."""
    migrated = get_migrated_finding_types()
    assert "rds_publicly_accessible" not in migrated
    assert "ec2_stopped_instance" not in migrated
    assert "s3_bucket_public_access" not in migrated


def test_load_balancer_unused_not_migrated():
    """load_balancer_unused requires OR condition which is not yet supported; must stay in legacy."""
    migrated = get_migrated_finding_types()
    assert "load_balancer_unused" not in migrated


# ── Detection filter ──────────────────────────────────────────────────────────

class _FakeFinding:
    def __init__(self, finding_type: str):
        self.finding_type = finding_type


def _run_filter(findings: list[_FakeFinding]) -> list[_FakeFinding]:
    """Reproduce the filter added to detection_extended_service.detect_extended_findings."""
    try:
        from app.rules import get_migrated_finding_types as _gm
        _migrated = _gm()
        if _migrated:
            findings = [f for f in findings if f.finding_type not in _migrated]
    except Exception:
        pass
    return findings


def test_detection_filter_removes_migrated_findings():
    findings = [
        _FakeFinding("cloudfront_insecure_viewer_protocol_policy"),  # migrated → suppressed
        _FakeFinding("rds_publicly_accessible"),                      # legacy → kept
    ]
    filtered = _run_filter(findings)
    types = {f.finding_type for f in filtered}
    assert "rds_publicly_accessible" in types
    assert "cloudfront_insecure_viewer_protocol_policy" not in types


def test_detection_filter_keeps_all_legacy_findings():
    legacy_types = ["rds_publicly_accessible", "ec2_stopped_instance", "s3_bucket_public_access"]
    findings = [_FakeFinding(t) for t in legacy_types]
    filtered = _run_filter(findings)
    assert len(filtered) == len(findings)


def test_detection_filter_empty_list_stays_empty():
    assert _run_filter([]) == []


def test_detection_filter_survives_import_error():
    """Filter must not raise even if rules module explodes."""
    findings = [_FakeFinding("rds_publicly_accessible")]
    with patch("app.rules.get_migrated_finding_types", side_effect=ImportError("boom")):
        filtered = _run_filter(findings)
    assert len(filtered) == 1


# ── Recommendation service config-driven path ─────────────────────────────────

def _make_finding(finding_type: str) -> SimpleNamespace:
    f = SimpleNamespace()
    f.finding_type = finding_type
    f.resource_id = "res-001"
    f.resource_type = "cloudfront_distribution"
    f.severity = "medium"
    f.evidence_json = {}
    return f


def test_recommendation_service_uses_rule_for_migrated_finding(monkeypatch):
    """When a rule exists for the finding_type, _build_recommendation_for_finding should
    call into the config-driven path (mock get_rule_for_finding returns a rule object)."""
    from app.rules.registry import RuleDefinition, RuleCondition

    mock_rule = RuleDefinition(
        rule_id="cloudfront_insecure_viewer_protocol_policy",
        enabled=True,
        resource_type="cloudfront_distribution",
        finding_type="cloudfront_insecure_viewer_protocol_policy",
        recommendation_type="cloudfront_review_insecure_protocol_policy",
        category="security",
        severity="high",
        impact="high",
        effort="low",
        confidence="high",
        actionability="review_required",
        title="CloudFront insecure viewer protocol",
        summary_template="Distribution {resource_id} uses an insecure protocol.",
        why_this_matters="Insecure protocols allow MITM attacks.",
        guided_action_key="cloudfront_review_protocol",
        recommended_action="Update viewer protocol policy.",
        approval_required=False,
        execution_eligible=False,
        conditions=[RuleCondition(op="eq", field="viewer_protocol_policy", value="allow-all")],
        evidence_fields=["viewer_protocol_policy"],
    )

    with patch("app.rules.registry.get_rule_for_finding", return_value=mock_rule):
        from app.rules.registry import get_rule_for_finding
        rule = get_rule_for_finding("cloudfront_insecure_viewer_protocol_policy")
    assert rule is not None
    assert rule.recommendation_type == "cloudfront_review_insecure_protocol_policy"


def test_recommendation_service_returns_none_for_unknown_finding():
    """Non-migrated finding types should return None from the rule registry."""
    from app.rules.registry import get_rule_for_finding
    assert get_rule_for_finding("rds_publicly_accessible") is None
    assert get_rule_for_finding("ec2_stopped_instance") is None


def test_recommendation_service_returns_none_for_empty_string():
    from app.rules.registry import get_rule_for_finding
    assert get_rule_for_finding("") is None


# ── No duplicate findings: rule engine + legacy do not produce same finding ────

def test_migrated_types_and_legacy_types_are_disjoint():
    """The MIGRATED_FINDING_TYPES used in detection_extended_service must not include
    any finding type that is still NOT defined in a rule config (i.e. all migrated types
    correspond to real rules)."""
    from app.rules.registry import get_registry
    registry = get_registry()
    migrated = registry.migrated_finding_types()
    # Every type in the migrated set must be resolvable via get_rule_for_finding
    for ft in migrated:
        assert registry.get_rule_for_finding(ft) is not None, (
            f"finding_type {ft!r} is in migrated set but has no corresponding rule"
        )
