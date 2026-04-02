"""Regression tests for extended AWS governance mapping (no live AWS calls)."""

from app.services.recommendation_service import _GOVERNANCE_TAG_FINDING_TO_RECOMMENDATION, _RECOMMENDATION_SOURCE_FINDING_TYPES


def test_governance_tag_finding_types_have_recommendation_pairs():
    for finding_type in _GOVERNANCE_TAG_FINDING_TO_RECOMMENDATION:
        assert finding_type in _RECOMMENDATION_SOURCE_FINDING_TYPES


def test_extended_finding_types_in_recommendation_sources():
    for ft in (
        "ec2_missing_required_tags",
        "ec2_stopped_instance",
        "acm_certificate_expiring_soon",
        "cloudfront_distribution_missing_required_tags",
        "cloudfront_insecure_viewer_protocol_policy",
        "cloudfront_missing_https_redirect",
        "cloudfront_disabled_distribution_review",
        "eventbridge_rule_without_targets",
        "eventbridge_rule_disabled_review",
        "apigateway_public_exposure_review",
        "acm_certificate_pending_validation",
        "acm_certificate_validation_issue",
        "ses_identity_unverified",
        "ses_sending_disabled_identity",
        "security_group_world_open_sensitive_port",
        "security_group_overly_permissive",
        "lambda_outdated_runtime",
        "lambda_review_timeout_configuration",
    ):
        assert ft in _RECOMMENDATION_SOURCE_FINDING_TYPES
