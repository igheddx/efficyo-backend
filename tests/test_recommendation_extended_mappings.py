from datetime import datetime, timezone
from uuid import uuid4

from app.models.finding import Finding
from app.services.recommendation_service import _build_recommendation_for_finding


def _finding(finding_type: str, resource_type: str) -> Finding:
    return Finding(
        id=uuid4(),
        tenant_id=uuid4(),
        cloud_account_id=uuid4(),
        resource_snapshot_id=uuid4(),
        resource_id="resource-1",
        resource_type=resource_type,
        finding_type=finding_type,
        severity="medium",
        evidence_json={},
    )


def test_extended_finding_types_map_to_recommendations(db):  # noqa: ARG001 - fixture initializes ORM mappings
    created_at = datetime.now(timezone.utc)
    cases = [
        ("cloudfront_missing_https_redirect", "cloudfront_distribution", "cloudfront_enforce_https_redirect"),
        ("cloudfront_disabled_distribution_review", "cloudfront_distribution", "cloudfront_review_disabled_distribution"),
        ("eventbridge_rule_without_targets", "eventbridge_rule", "eventbridge_add_targets_or_cleanup"),
        ("eventbridge_rule_disabled_review", "eventbridge_rule", "eventbridge_review_disabled_rule"),
        ("acm_certificate_pending_validation", "acm_certificate", "acm_complete_validation"),
        ("acm_certificate_validation_issue", "acm_certificate", "acm_investigate_validation_failure"),
        ("ses_identity_unverified", "ses_email_identity", "ses_fix_identity_verification"),
        ("ses_sending_disabled_identity", "ses_email_identity", "ses_review_sending_configuration"),
        ("security_group_world_open_sensitive_port", "security_group", "security_group_restrict_world_open_ports"),
        ("security_group_overly_permissive", "security_group", "security_group_restrict_ingress"),
        ("load_balancer_deletion_protection_disabled", "load_balancer", "load_balancer_enable_deletion_protection"),
        ("load_balancer_no_healthy_targets", "load_balancer", "load_balancer_review_target_health"),
        ("load_balancer_no_healthy_traffic_path", "load_balancer", "load_balancer_review_target_health"),
        ("load_balancer_unused", "load_balancer", "load_balancer_cleanup_unused"),
        ("load_balancer_partial_failure", "load_balancer", "load_balancer_review_target_health"),
        ("route_table_unassociated_review", "route_table", "route_table_cleanup_unused"),
        ("route_table_public_default_route_review", "route_table", "route_table_review_public_egress"),
        ("lambda_outdated_runtime", "lambda_function", "lambda_update_runtime"),
        ("lambda_review_timeout_configuration", "lambda_function", "lambda_review_timeout_configuration"),
        ("target_group_no_targets", "target_group", "attach_targets_or_cleanup"),
        ("target_group_unhealthy_targets", "target_group", "investigate_unhealthy_targets"),
        ("target_group_misconfigured_health_check", "target_group", "fix_health_check_configuration"),
        ("rds_low_cpu_underutilized", "rds_instance", "downsize_instance"),
        ("rds_high_storage_unused", "rds_instance", "switch_instance_class"),
        ("rds_idle_instance_candidate", "rds_instance", "stop_or_schedule"),
        ("s3_missing_encryption", "s3_bucket", "enable_encryption"),
        ("s3_no_lifecycle_large_bucket", "s3_bucket", "add_lifecycle_policy"),
        ("s3_infrequent_access_candidate", "s3_bucket", "move_to_ia_or_glacier"),
        ("lambda_low_utilization", "lambda_function", "reduce_memory"),
        ("lambda_excessive_memory_allocation", "lambda_function", "reduce_memory"),
        ("lambda_concurrency_risk", "lambda_function", "set_reserved_concurrency"),
        ("public_subnet_with_open_sg", "security_group", "security_group_restrict_world_open_ports"),
        ("internet_exposed_resource_chain", "route_table", "security_group_restrict_world_open_ports"),
    ]
    for finding_type, resource_type, expected in cases:
        rec = _build_recommendation_for_finding(uuid4(), uuid4(), _finding(finding_type, resource_type), created_at)
        assert rec is not None
        assert rec.recommendation_type == expected
