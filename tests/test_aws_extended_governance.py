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
    ):
        assert ft in _RECOMMENDATION_SOURCE_FINDING_TYPES
