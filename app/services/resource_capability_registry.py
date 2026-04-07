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
    "ecs_cluster",
    "ecs_service",
    "eks_cluster",
    "elasticache_cluster",
    "redshift_cluster",
    "opensearch_domain",
    "sqs_queue",
    "sns_topic",
    "kinesis_stream",
    "dynamodb_table",
    "kms_key",
    "cloudwatch_log_group",
    "iam_role",
    "iam_user",
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
    "ecs_cluster",
    "ecs_service",
    "eks_cluster",
    "elasticache_cluster",
    "redshift_cluster",
    "opensearch_domain",
    "sqs_queue",
    "sns_topic",
    "kinesis_stream",
    "dynamodb_table",
    "kms_key",
    "cloudwatch_log_group",
    "iam_role",
    "iam_user",
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
    "ecs_cluster": ("ecs_cluster_add_required_tags", "ECS cluster"),
    "ecs_service": ("ecs_service_add_required_tags", "ECS service"),
    "eks_cluster": ("eks_cluster_add_required_tags", "EKS cluster"),
    "elasticache_cluster": ("elasticache_cluster_add_required_tags", "ElastiCache cluster"),
    "redshift_cluster": ("redshift_cluster_add_required_tags", "Redshift cluster"),
    "opensearch_domain": ("opensearch_domain_add_required_tags", "OpenSearch domain"),
    "sqs_queue": ("sqs_queue_add_required_tags", "SQS queue"),
    "sns_topic": ("sns_topic_add_required_tags", "SNS topic"),
    "kinesis_stream": ("kinesis_stream_add_required_tags", "Kinesis stream"),
    "dynamodb_table": ("dynamodb_table_add_required_tags", "DynamoDB table"),
    "kms_key": ("kms_key_add_required_tags", "KMS key"),
    "cloudwatch_log_group": ("cloudwatch_log_group_add_required_tags", "CloudWatch log group"),
    "iam_role": ("iam_role_add_required_tags", "IAM role"),
    "iam_user": ("iam_user_add_required_tags", "IAM user"),
}

# finding_type -> (recommendation_type, human noun)
TAG_GOVERNANCE_FINDING_TO_RECOMMENDATION: dict[str, tuple[str, str]] = {
    f"{resource_type}_missing_required_tags": value
    for resource_type, value in TAG_GOVERNANCE_RESOURCE_SPECS.items()
}
