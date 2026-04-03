"""Central registry for resource ingestion/detection/recommendation coverage.

This module is intentionally lightweight so service layers can import shared
coverage definitions without circular dependencies.
"""

from __future__ import annotations

# All resource snapshot types currently ingested by collectors.
SUPPORTED_SNAPSHOT_RESOURCE_TYPES: tuple[str, ...] = (
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
    "target_group",
    "route_table",
    "vpc",
    "subnet",
    "nat_gateway",
    "internet_gateway",
    "security_group",
    "rds_parameter_group",
)

# Extended (non-core) resource types that receive required-tag governance checks.
EXTENDED_TAGGABLE_RESOURCE_TYPES: tuple[str, ...] = (
    "cloudfront_distribution",
    "acm_certificate",
    "apigateway_rest_api",
    "apigateway_http_api",
    "eventbridge_rule",
    "ses_email_identity",
    "iot_thing",
    "load_balancer",
    "target_group",
    "route_table",
    "vpc",
    "subnet",
    "nat_gateway",
    "internet_gateway",
    "security_group",
    "rds_parameter_group",
)

# Resource type -> (recommendation_type, human noun for copy)
TAG_GOVERNANCE_RESOURCE_SPECS: dict[str, tuple[str, str]] = {
    "ec2": ("ec2_add_required_tags", "EC2 instance"),
    "cloudfront_distribution": ("cloudfront_add_required_tags", "CloudFront distribution"),
    "acm_certificate": ("acm_add_required_tags", "ACM certificate"),
    "apigateway_rest_api": ("apigateway_add_required_tags", "API Gateway REST API"),
    "apigateway_http_api": ("apigateway_http_add_required_tags", "API Gateway HTTP API"),
    "eventbridge_rule": ("eventbridge_add_required_tags", "EventBridge rule"),
    "ses_email_identity": ("ses_add_required_tags", "SES identity"),
    "iot_thing": ("iot_add_required_tags", "IoT thing"),
    "load_balancer": ("load_balancer_add_required_tags", "load balancer"),
    "target_group": ("target_group_add_required_tags", "target group"),
    "route_table": ("route_table_add_required_tags", "route table"),
    "vpc": ("vpc_add_required_tags", "VPC"),
    "subnet": ("subnet_add_required_tags", "subnet"),
    "nat_gateway": ("nat_gateway_add_required_tags", "NAT gateway"),
    "internet_gateway": ("internet_gateway_add_required_tags", "internet gateway"),
    "security_group": ("security_group_add_required_tags", "security group"),
    "rds_parameter_group": ("rds_parameter_group_add_required_tags", "RDS parameter group"),
}

# finding_type -> (recommendation_type, human noun)
TAG_GOVERNANCE_FINDING_TO_RECOMMENDATION: dict[str, tuple[str, str]] = {
    f"{resource_type}_missing_required_tags": value
    for resource_type, value in TAG_GOVERNANCE_RESOURCE_SPECS.items()
}
