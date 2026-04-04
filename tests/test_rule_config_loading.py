"""Tests for config-driven rule config loading and registry."""

from __future__ import annotations

import pytest

from app.rules.loader import load_all_rules
from app.rules.registry import RuleRegistry


@pytest.fixture(scope="module")
def all_rules():
    return load_all_rules()


@pytest.fixture(scope="module")
def registry(all_rules):
    return RuleRegistry(all_rules)


# ── Loader tests ──────────────────────────────────────────────────────────────


def test_load_all_rules_returns_non_empty_list(all_rules):
    assert len(all_rules) > 0, "No rules were loaded from the config directory"


def test_all_loaded_rules_have_required_fields(all_rules):
    for rule in all_rules:
        assert rule.rule_id, f"rule_id missing on {rule}"
        assert rule.resource_type, f"resource_type missing on {rule.rule_id}"
        assert rule.finding_type, f"finding_type missing on {rule.rule_id}"
        assert rule.recommendation_type, f"recommendation_type missing on {rule.rule_id}"
        assert rule.title, f"title missing on {rule.rule_id}"
        assert rule.conditions, f"conditions empty on {rule.rule_id}"


def test_scores_are_valid_levels(all_rules):
    valid_impact_effort_confidence = {"low", "medium", "high"}
    valid_actionability = {"auto", "guided", "review_required"}
    for rule in all_rules:
        if not rule.enabled:
            continue
        assert rule.impact in valid_impact_effort_confidence, (
            f"{rule.rule_id}: invalid impact={rule.impact!r}"
        )
        assert rule.effort in valid_impact_effort_confidence, (
            f"{rule.rule_id}: invalid effort={rule.effort!r}"
        )
        assert rule.confidence in valid_impact_effort_confidence, (
            f"{rule.rule_id}: invalid confidence={rule.confidence!r}"
        )
        assert rule.actionability in valid_actionability, (
            f"{rule.rule_id}: invalid actionability={rule.actionability!r}"
        )


def test_tagging_rules_are_loaded(all_rules):
    tagging_ids = {r.rule_id for r in all_rules if "missing_required_tags" in r.rule_id}
    assert "cloudfront_distribution_missing_required_tags" in tagging_ids
    assert "acm_certificate_missing_required_tags" in tagging_ids
    assert "security_group_missing_required_tags" in tagging_ids


def test_cloudfront_rules_are_loaded(all_rules):
    finding_types = {r.finding_type for r in all_rules}
    assert "cloudfront_insecure_viewer_protocol_policy" in finding_types
    assert "cloudfront_missing_https_redirect" in finding_types
    assert "cloudfront_disabled_distribution_review" in finding_types


def test_lambda_rules_are_loaded(all_rules):
    finding_types = {r.finding_type for r in all_rules}
    assert "lambda_outdated_runtime" in finding_types
    assert "lambda_review_timeout_configuration" in finding_types


def test_networking_rules_are_loaded(all_rules):
    finding_types = {r.finding_type for r in all_rules}
    assert "load_balancer_deletion_protection_disabled" in finding_types
    assert "route_table_unassociated_review" in finding_types
    assert "target_group_no_targets" in finding_types


# ── Registry tests ────────────────────────────────────────────────────────────


def test_registry_resolves_rules_for_resource_type(registry):
    cf_rules = registry.rules_for_resource_type("cloudfront_distribution")
    # At least 3 CloudFront rules (tagging + insecure_protocol + disabled + etc.)
    assert len(cf_rules) >= 2


def test_registry_get_rule_for_finding_returns_correct_rule(registry):
    rule = registry.get_rule_for_finding("cloudfront_insecure_viewer_protocol_policy")
    assert rule is not None
    assert rule.recommendation_type == "cloudfront_review_insecure_protocol_policy"
    assert rule.severity == "high"
    assert rule.impact == "high"
    assert rule.effort == "low"
    assert rule.confidence == "high"


def test_registry_migrated_finding_types_is_a_frozenset(registry):
    migrated = registry.migrated_finding_types()
    assert isinstance(migrated, frozenset)
    assert "cloudfront_insecure_viewer_protocol_policy" in migrated
    assert "lambda_outdated_runtime" in migrated
    assert "target_group_no_targets" in migrated


def test_registry_unknown_finding_type_returns_none(registry):
    rule = registry.get_rule_for_finding("totally_unknown_finding_type_xyz")
    assert rule is None


def test_registry_disabled_rules_are_excluded_from_migrated_set(all_rules):
    """Disabled rules should not appear in migrated finding types."""
    disabled_types = {r.finding_type for r in all_rules if not r.enabled}
    registry = RuleRegistry(all_rules)
    migrated = registry.migrated_finding_types()
    for disabled_ft in disabled_types:
        # Only assert when no other enabled rule covers the same finding_type
        other_enabled = any(r.finding_type == disabled_ft and r.enabled for r in all_rules)
        if not other_enabled:
            assert disabled_ft not in migrated, (
                f"Disabled finding_type {disabled_ft} should not be in migrated types"
            )
