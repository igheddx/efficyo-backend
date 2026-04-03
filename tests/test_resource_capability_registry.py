"""Coverage guardrails for resource capability wiring."""

from app.services.detection_extended_service import EXTENDED_TAGGABLE_RESOURCE_TYPES
from app.services.recommendation_service import _RECOMMENDATION_SOURCE_FINDING_TYPES
from app.services.resource_capability_registry import (
    SUPPORTED_SNAPSHOT_RESOURCE_TYPES,
    TAG_GOVERNANCE_FINDING_TO_RECOMMENDATION,
    TAG_GOVERNANCE_RESOURCE_SPECS,
)


def test_supported_snapshot_types_include_core_and_extended_services():
    for rt in (
        "ec2_instance",
        "rds_instance",
        "aurora_cluster",
        "lambda_function",
        "s3_bucket",
        "ebs_volume",
        "cloudfront_distribution",
        "acm_certificate",
        "apigateway_rest_api",
        "apigateway_http_api",
        "eventbridge_rule",
        "ses_email_identity",
        "iot_thing",
        "load_balancer",
        "route_table",
        "vpc",
        "subnet",
        "nat_gateway",
        "internet_gateway",
        "security_group",
    ):
        assert rt in SUPPORTED_SNAPSHOT_RESOURCE_TYPES


def test_extended_taggable_resources_have_tag_recommendation_specs():
    for rt in EXTENDED_TAGGABLE_RESOURCE_TYPES:
        assert rt in TAG_GOVERNANCE_RESOURCE_SPECS


def test_tag_governance_findings_are_recommendation_sources():
    for finding_type in TAG_GOVERNANCE_FINDING_TO_RECOMMENDATION:
        assert finding_type in _RECOMMENDATION_SOURCE_FINDING_TYPES
