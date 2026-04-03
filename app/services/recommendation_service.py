"""Recommendation service for RDS findings."""

from dataclasses import dataclass
from datetime import datetime
import logging
import re
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.cost_window import round_currency
from app.core.db import utc_now
from app.models.finding import Finding
from app.models.recommendation import Recommendation
from app.models.recommendation_outcome import RecommendationOutcome
from app.schemas.cloud_account import RecommendationRead
from app.services.ai_explanation_service import generate_ai_explanation
from app.services.recommendation_credibility import (
    computed_impact_score,
    decision_factors_for,
    confidence_reason_for,
    effective_confidence_reason,
    effective_savings_basis,
    priority_bucket_for,
    rank_by_computed_score,
    ranking_reason_for,
    savings_basis_for,
    why_it_matters_for,
)
from app.services import detection_service, recommendation_intelligence_service, recommendation_outcome_service
from app.services.cloud_account_service import get_cloud_account_or_raise as _get_cloud_account_or_raise
from app.services.resource_capability_registry import TAG_GOVERNANCE_FINDING_TO_RECOMMENDATION


logger = logging.getLogger(__name__)


@dataclass
class RecommendationRunResult:
    """Result of a recommendation generation run."""

    cloud_account_id: UUID
    recommendations_created: int
    created_at: datetime


@dataclass
class TopOpportunity:
    """A ranked recommendation opportunity with computed impact score."""

    recommendation_id: UUID
    resource_id: str
    resource_type: str
    recommendation_type: str
    recommendation_category: str
    summary: str
    ai_explanation: str | None
    estimated_savings: float | None
    risk_level: str
    confidence_score: str
    computed_score: float
    normalized_savings: float
    risk_factor: float
    confidence_factor: float
    urgency_factor: float
    ranking_reason: str
    priority_bucket: str
    savings_basis: str
    confidence_reason: str
    why_it_matters: str
    learned_confidence: str | None
    learned_confidence_reason: str | None
    historical_success_rate: float | None
    avg_realized_savings_for_type: float | None
    steps: list[str]
    estimated_time: str
    difficulty: str


def guided_actions_for_type(recommendation_type: str) -> tuple[list[str], str, str]:
    rtype = (recommendation_type or "").lower()

    if rtype == "nat_gateway_cost_review":
        return (
            [
                "Identify services using NAT Gateway",
                "Evaluate if traffic can use VPC endpoints (S3, DynamoDB)",
                "Create VPC endpoints where applicable",
                "Update route tables to bypass NAT",
                "Monitor traffic and remove unused NAT Gateway",
            ],
            "30-60 minutes",
            "medium",
        )

    if rtype == "aurora_serverless_cost_review":
        return (
            [
                "Review current ACU min/max settings",
                "Analyze database usage patterns",
                "Lower minimum capacity if underutilized",
                "Monitor performance metrics (CPU, connections)",
                "Adjust scaling thresholds safely",
            ],
            "20-40 minutes",
            "medium",
        )

    if rtype == "lambda_rightsize_memory":
        return (
            [
                "Review current memory configuration",
                "Analyze execution duration and memory usage",
                "Reduce memory incrementally",
                "Test function performance",
                "Deploy updated configuration",
            ],
            "10-20 minutes",
            "easy",
        )

    if rtype == "s3_enable_public_access_block":
        return (
            [
                "Navigate to S3 bucket settings",
                "Enable all Public Access Block options",
                "Validate bucket policy",
                "Test access behavior",
            ],
            "5-10 minutes",
            "easy",
        )

    if rtype in {
        "ec2_add_required_tags",
        "cloudfront_add_required_tags",
        "acm_add_required_tags",
        "apigateway_add_required_tags",
        "apigateway_http_add_required_tags",
        "eventbridge_add_required_tags",
        "ses_add_required_tags",
        "iot_add_required_tags",
        "vpc_add_required_tags",
        "subnet_add_required_tags",
        "nat_gateway_add_required_tags",
        "internet_gateway_add_required_tags",
        "security_group_add_required_tags",
        "load_balancer_add_required_tags",
        "route_table_add_required_tags",
    }:
        return (
            [
                "Open the resource in the AWS console or update IaC",
                "Add Name and Environment tags per your tagging standard",
                "Confirm ownership and cost allocation mappings",
            ],
            "5-15 minutes",
            "easy",
        )

    if rtype == "ec2_review_stopped_instance":
        return (
            [
                "Confirm whether the instance is still required",
                "Snapshot or back up data before termination",
                "Terminate unused instances or start them if still needed",
            ],
            "15-30 minutes",
            "medium",
        )

    if rtype == "acm_review_certificate_expiry":
        return (
            [
                "Open ACM and review validation status for the certificate",
                "Renew or re-validate DNS before the not-after date",
                "Update dependent CloudFront, ALB, or API Gateway associations if the ARN changes",
            ],
            "30-60 minutes",
            "medium",
        )

    if rtype == "cloudfront_review_insecure_protocol_policy":
        return (
            [
                "Open CloudFront distribution behavior settings",
                "Set viewer protocol policy to redirect HTTP to HTTPS",
                "Validate origin/app compatibility and deploy",
            ],
            "10-20 minutes",
            "easy",
        )

    if rtype == "cloudfront_enforce_https_redirect":
        return (
            [
                "Review CloudFront default and ordered cache behaviors",
                "Set viewer protocol policy to redirect HTTP to HTTPS",
                "Re-test all public paths and API endpoints over HTTPS",
            ],
            "10-20 minutes",
            "easy",
        )

    if rtype == "cloudfront_review_disabled_distribution":
        return (
            [
                "Confirm whether the distribution should remain disabled",
                "Check dependent DNS records and certificate validity",
                "Enable only when traffic and origin posture are validated",
            ],
            "10-20 minutes",
            "easy",
        )

    if rtype == "apigateway_public_exposure_review":
        return (
            [
                "Review API auth and authorization model",
                "Confirm throttling/logging and stage controls",
                "Validate downstream Lambda/resource permissions",
            ],
            "20-40 minutes",
            "medium",
        )

    if rtype == "eventbridge_add_targets_or_cleanup":
        return (
            [
                "Verify whether the rule is expected to trigger workloads",
                "Attach the intended targets or remove stale rule definitions",
                "Re-run a test event to confirm target delivery",
            ],
            "15-30 minutes",
            "medium",
        )

    if rtype == "eventbridge_review_disabled_rule":
        return (
            [
                "Confirm whether the disabled rule is intentionally paused",
                "Re-enable the rule if it should be active",
                "Document owner and expected schedule/event pattern",
            ],
            "10-20 minutes",
            "easy",
        )

    if rtype == "load_balancer_enable_deletion_protection":
        return (
            [
                "Open EC2 > Load Balancers and select the impacted load balancer",
                "Enable deletion protection in load balancer attributes",
                "Apply and verify the attribute is persisted",
            ],
            "5-10 minutes",
            "easy",
        )

    if rtype == "load_balancer_review_target_health":
        return (
            [
                "Open associated target groups and inspect target health reasons",
                "Restore service endpoints, health check paths, or security-group rules",
                "Validate healthy targets before closing the incident",
            ],
            "15-30 minutes",
            "medium",
        )

    if rtype == "route_table_cleanup_unused":
        return (
            [
                "Review whether the route table is intentionally reserved",
                "Confirm it has no active subnet associations",
                "Delete stale route tables or document ownership if retained",
            ],
            "10-20 minutes",
            "easy",
        )

    if rtype == "route_table_review_public_egress":
        return (
            [
                "Identify subnets associated with this route table",
                "Validate whether direct internet egress is intended",
                "For private workloads, route default traffic via NAT or remove the public default route",
            ],
            "15-30 minutes",
            "medium",
        )

    if rtype == "lambda_update_runtime":
        return (
            [
                "Identify supported target runtime for the function",
                "Run tests against the updated runtime in non-production",
                "Deploy runtime update and monitor cold starts/errors",
            ],
            "30-60 minutes",
            "medium",
        )

    if rtype == "lambda_review_timeout_configuration":
        return (
            [
                "Review invocation duration and timeout failures in CloudWatch",
                "Tune timeout to match expected execution profile",
                "Add retries/dead-letter handling if long-running paths remain",
            ],
            "20-40 minutes",
            "medium",
        )

    if rtype == "acm_complete_validation":
        return (
            [
                "Review ACM validation method and pending records",
                "Create or correct DNS/email validation records",
                "Confirm certificate reaches ISSUED before cutover deadlines",
            ],
            "20-40 minutes",
            "medium",
        )

    if rtype == "acm_investigate_validation_failure":
        return (
            [
                "Inspect ACM status and validation failure reason",
                "Re-request or re-import certificate with correct domains",
                "Update dependent services after certificate remediation",
            ],
            "30-60 minutes",
            "medium",
        )

    if rtype == "ses_fix_identity_verification":
        return (
            [
                "Open SES identity settings and confirm verification type",
                "Add or repair DNS verification records",
                "Verify identity status transitions to SUCCESS",
            ],
            "15-30 minutes",
            "easy",
        )

    if rtype == "ses_review_sending_configuration":
        return (
            [
                "Check why sending is disabled for the identity",
                "Review account-level SES sending limits and suppression posture",
                "Enable sending only when controls are validated",
            ],
            "15-30 minutes",
            "medium",
        )

    if rtype == "security_group_restrict_world_open_ports":
        return (
            [
                "Identify required source CIDRs for admin/application access",
                "Replace 0.0.0.0/0 or ::/0 ingress with least-privilege ranges",
                "Validate connectivity after rule updates",
            ],
            "20-40 minutes",
            "medium",
        )

    if rtype == "security_group_restrict_ingress":
        return (
            [
                "Review current ingress rules and remove stale entries",
                "Consolidate to minimal required ports/protocols",
                "Record ownership and intended exposure per rule",
            ],
            "20-40 minutes",
            "medium",
        )

    if rtype == "target_group_review_target_health":
        return (
            [
                "Open the target group and review target health descriptions",
                "Identify why targets are unhealthy (failed health checks, registration in progress)",
                "Restore endpoints, fix health check paths, or adjust security group rules",
                "Verify all targets reach healthy state before closing",
            ],
            "20-40 minutes",
            "medium",
        )

    if rtype == "target_group_enable_stickiness":
        return (
            [
                "Open the target group attributes in EC2 console",
                "Enable stickiness and choose duration (1 hour recommended)",
                "Test application behavior to confirm session persistence",
            ],
            "5-15 minutes",
            "easy",
        )

    if rtype == "target_group_optimize_deregistration_delay":
        return (
            [
                "Open the target group attributes in EC2 console",
                "Reduce deregistration delay to 30-60 seconds if workload allows",
                "Test graceful shutdown behavior before applying in production",
            ],
            "5-15 minutes",
            "easy",
        )

    if rtype == "rds_parameter_group_enable_slow_query_log":
        return (
            [
                "Open RDS parameter group in AWS console",
                "Edit the parameter group and set slow_query_log = 1",
                "Set long_query_time to desired threshold (e.g., 2 seconds)",
                "Apply changes to databases using this parameter group",
                "Monitor MySQL slow query log in CloudWatch Logs",
            ],
            "15-30 minutes",
            "easy",
        )

    if rtype == "rds_parameter_group_disable_general_log":
        return (
            [
                "Open RDS parameter group in AWS console",
                "Edit the parameter group and set general_log = 0",
                "Apply changes to databases using this parameter group",
                "Verify query performance improves after the change",
            ],
            "10-20 minutes",
            "easy",
        )

    return (
        [
            "Review resource configuration",
            "Apply recommended change cautiously",
            "Monitor impact after change",
        ],
        "varies",
        "medium",
    )


def _credibility_pair(recommendation_type: str, recommendation_category: str, estimated_savings) -> tuple[str, str]:
    return (
        savings_basis_for(recommendation_type, recommendation_category),
        confidence_reason_for(estimated_savings),
    )


def recommendation_read_from_orm(
    rec: Recommendation,
    rank: int | None,
    evidence_json: dict | None = None,
) -> RecommendationRead:
    """Build API model with rank-derived why_it_matters (not stored on the row)."""
    steps, estimated_time, difficulty = guided_actions_for_type(rec.recommendation_type)
    return RecommendationRead(
        id=rec.id,
        resource_id=rec.resource_id,
        resource_type=rec.resource_type,
        recommendation_type=rec.recommendation_type,
        recommendation_category=rec.recommendation_category,
        summary=rec.summary,
        ai_explanation=(
            generate_ai_explanation(
                recommendation_type=rec.recommendation_type,
                resource_id=rec.resource_id,
                estimated_savings=rec.estimated_savings,
                risk_level=rec.risk_level,
                recommended_action=rec.recommended_action,
                evidence_json=evidence_json,
            )
            or rec.explanation
        ),
        explanation=rec.explanation,
        risk_level=rec.risk_level,
        confidence_score=rec.confidence_score,
        recommended_action=rec.recommended_action,
        estimated_savings=(
            round_currency(rec.estimated_savings) if rec.estimated_savings is not None else None
        ),
        created_at=rec.created_at,
        savings_basis=effective_savings_basis(rec),
        confidence_reason=effective_confidence_reason(rec),
        why_it_matters=why_it_matters_for(rank, rec.recommendation_category),
        learned_confidence=None,
        learned_confidence_reason=None,
        historical_success_rate=None,
        avg_realized_savings_for_type=None,
        steps=steps,
        estimated_time=estimated_time,
        difficulty=difficulty,
        workflow_status=None,
        approved_by=None,
        approved_at=None,
        applied_by=None,
        applied_at=None,
        execution_notes=None,
        evidence_json=evidence_json or None,
    )


def _enrich_recommendation_read(
    read: RecommendationRead,
    rec: Recommendation,
    stats_by_type: dict,
    outcome_by_recommendation_id: dict[UUID, RecommendationOutcome] | None = None,
) -> RecommendationRead:
    layer = recommendation_intelligence_service.learned_intelligence_for_recommendation(rec, stats_by_type)
    outcome = (outcome_by_recommendation_id or {}).get(rec.id)
    workflow_layer = {}
    if outcome is not None:
        workflow_layer = {
            "workflow_status": recommendation_outcome_service.normalized_workflow_status(outcome.workflow_status),
            "approved_by": outcome.approved_by,
            "approved_role": outcome.approved_role,
            "approved_at": outcome.approved_at,
            "approval_comment": outcome.approval_comment,
            "rejection_reason": outcome.rejection_reason,
            "rejected_at": outcome.rejected_at,
            "rejected_by": outcome.rejected_by,
            "applied_by": outcome.applied_by,
            "applied_role": outcome.applied_role,
            "applied_at": outcome.applied_at,
            "execution_notes": outcome.execution_notes,
        }
    return read.model_copy(update={**layer, **workflow_layer})


def _nat_gateway_explanation(finding: Finding) -> str:
    evidence = finding.evidence_json or {}
    raw = evidence.get("current_monthly_cost")
    if raw is not None:
        try:
            amt = f"${float(raw):,.2f}"
        except (TypeError, ValueError):
            amt = "approximately $0.00"
    else:
        amt = "a meaningful amount"
    return (
        f"You are spending approximately {amt}/month on NAT Gateway. "
        "If traffic is low or predictable, consider using VPC endpoints or a NAT instance to reduce cost."
    )


def _aurora_serverless_explanation(finding: Finding) -> str:
    if finding.estimated_savings is not None:
        try:
            x = float(finding.estimated_savings)
            driver = f"This database is a major cost driver (~${x:,.2f}/month)."
        except (TypeError, ValueError):
            driver = "This database is a major cost driver."
    else:
        driver = "This database is a major cost driver."
    return f"{driver} Serverless scaling may be higher than needed for current workload."


def _recommendation_category_for_type(recommendation_type: str) -> str:
    if recommendation_type in {
        "aurora_serverless_cost_review",
        "lambda_rightsize_memory",
        "s3_add_lifecycle_policy",
        "nat_gateway_cost_review",
        "waf_cost_review",
        "ec2_review_stopped_instance",
    }:
        return "cost"
    if recommendation_type in {
        "rds_disable_public_access",
        "s3_enable_public_access_block",
        "s3_enable_versioning",
        "acm_review_certificate_expiry",
        "cloudfront_review_insecure_protocol_policy",
        "cloudfront_enforce_https_redirect",
        "cloudfront_review_disabled_distribution",
        "apigateway_public_exposure_review",
        "acm_complete_validation",
        "acm_investigate_validation_failure",
        "lambda_update_runtime",
        "lambda_review_timeout_configuration",
        "ses_fix_identity_verification",
        "ses_review_sending_configuration",
        "security_group_restrict_world_open_ports",
        "security_group_restrict_ingress",
        "eventbridge_add_targets_or_cleanup",
        "eventbridge_review_disabled_rule",
        "load_balancer_enable_deletion_protection",
        "load_balancer_review_target_health",
        "route_table_review_public_egress",
        "target_group_review_target_health",
        "target_group_enable_stickiness",
        "target_group_optimize_deregistration_delay",
        "rds_parameter_group_enable_slow_query_log",
        "rds_parameter_group_disable_general_log",
    }:
        return "security"
    return "governance"


# finding_type -> (recommendation_type, human noun for copy)
_GOVERNANCE_TAG_FINDING_TO_RECOMMENDATION: dict[str, tuple[str, str]] = {
    **TAG_GOVERNANCE_FINDING_TO_RECOMMENDATION,
}


def _build_recommendation_for_finding(
    tenant_id: UUID,
    cloud_account_id: UUID,
    finding: Finding,
    created_at: datetime,
) -> Recommendation | None:
    estimated_savings = finding.estimated_savings

    tag_map = _GOVERNANCE_TAG_FINDING_TO_RECOMMENDATION.get(finding.finding_type)
    if tag_map:
        rtype, noun = tag_map
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary=f"Add required tags to {noun}",
            explanation=f"This {noun.lower()} is missing one or more required tags: Name, Environment.",
            risk_level="medium",
            confidence_score="high",
            recommended_action="Add Name and Environment tags in AWS for governance and allocation.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "ec2_stopped_instance":
        rtype = "ec2_review_stopped_instance"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Review stopped EC2 instance",
            explanation=(
                "This instance is in the stopped state. If it is no longer needed, terminate it or "
                "create an AMI snapshot before removal to avoid wasted EBS and management overhead."
            ),
            risk_level="low",
            confidence_score="medium",
            recommended_action="Confirm ownership, terminate if obsolete, or start if still required.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "acm_certificate_expiring_soon":
        evidence = finding.evidence_json or {}
        days = evidence.get("days_remaining")
        rtype = "acm_review_certificate_expiry"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        detail = f" Renews in about {days} days." if days is not None else ""
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Renew or replace ACM certificate before expiry",
            explanation=(
                "An ACM certificate is approaching expiration. Plan validation/renewal to avoid "
                "TLS or availability issues for dependent resources."
                + detail
            ),
            risk_level="high",
            confidence_score="high",
            recommended_action="Complete DNS validation or re-import; update dependent distributions/endpoints.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "cloudfront_insecure_viewer_protocol_policy":
        rtype = "cloudfront_review_insecure_protocol_policy"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Harden CloudFront viewer protocol policy",
            explanation=(
                "This distribution allows insecure viewer protocol behavior. "
                "Enforce HTTPS redirect or HTTPS-only for better transport security."
            ),
            risk_level="high",
            confidence_score="high",
            recommended_action="Update default cache behavior viewer protocol policy to redirect HTTP to HTTPS.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "cloudfront_missing_https_redirect":
        rtype = "cloudfront_enforce_https_redirect"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Enforce HTTPS redirect on CloudFront viewer paths",
            explanation=(
                "This distribution is not configured to redirect all viewer HTTP requests to HTTPS. "
                "Redirecting to HTTPS reduces accidental clear-text traffic."
            ),
            risk_level="medium",
            confidence_score="high",
            recommended_action="Set CloudFront viewer protocol policy to redirect-to-https on active behaviors.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "cloudfront_disabled_distribution_review":
        rtype = "cloudfront_review_disabled_distribution"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Review disabled CloudFront distribution state",
            explanation=(
                "This distribution is currently disabled. Confirm whether this is intentional or if "
                "traffic should be restored after validating origin and certificate posture."
            ),
            risk_level="low",
            confidence_score="medium",
            recommended_action="Validate ownership and enable only if this distribution is still in service.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "apigateway_public_exposure_review":
        rtype = "apigateway_public_exposure_review"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Review public API exposure and integration posture",
            explanation=(
                "This API endpoint is publicly reachable. Validate authentication, throttling, and downstream "
                "integration exposure before promoting traffic."
            ),
            risk_level="medium",
            confidence_score="medium",
            recommended_action="Review auth controls, stage settings, and integrated targets for least privilege.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "eventbridge_rule_without_targets":
        rtype = "eventbridge_add_targets_or_cleanup"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Attach EventBridge targets or remove stale rule",
            explanation=(
                "This EventBridge rule currently has no targets. Validate intended behavior and either "
                "attach a destination or remove unused automation."
            ),
            risk_level="medium",
            confidence_score="high",
            recommended_action="Add valid targets to the rule, or retire it if no longer required.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "eventbridge_rule_disabled_review":
        rtype = "eventbridge_review_disabled_rule"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Review disabled EventBridge rule",
            explanation="This rule is disabled. Confirm whether the disabled state is intentional and still valid.",
            risk_level="low",
            confidence_score="high",
            recommended_action="Enable the rule if active event automation is expected; otherwise document and keep disabled.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "load_balancer_deletion_protection_disabled":
        rtype = "load_balancer_enable_deletion_protection"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Enable deletion protection for load balancer",
            explanation=(
                "Deletion protection is disabled for this load balancer. Enabling it helps prevent "
                "accidental deletion that can cause availability incidents."
            ),
            risk_level="medium",
            confidence_score="high",
            recommended_action="Enable deletion protection in load balancer attributes.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "load_balancer_no_healthy_targets":
        rtype = "load_balancer_review_target_health"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Review unhealthy load balancer target groups",
            explanation=(
                "This load balancer currently has no healthy targets. Review target-group health, "
                "backend readiness, and health-check configuration to restore traffic reliability."
            ),
            risk_level="high",
            confidence_score="high",
            recommended_action="Fix target health and validate at least one healthy backend per target group.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "route_table_unassociated_review":
        rtype = "route_table_cleanup_unused"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Review or clean up unassociated route table",
            explanation=(
                "This route table has no subnet associations and may be stale configuration. "
                "Review ownership and remove if unused."
            ),
            risk_level="low",
            confidence_score="high",
            recommended_action="Delete unused route table or document intended ownership and purpose.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "route_table_public_default_route_review":
        rtype = "route_table_review_public_egress"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Review route table public default egress",
            explanation=(
                "This route table sends default traffic directly to an internet gateway. "
                "Verify this is intended for associated subnets and private workload boundaries."
            ),
            risk_level="medium",
            confidence_score="medium",
            recommended_action="Validate subnet intent and route default traffic through NAT where appropriate.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "acm_certificate_pending_validation":
        rtype = "acm_complete_validation"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Complete ACM certificate validation",
            explanation="This ACM certificate is pending validation and may not be usable for dependent endpoints yet.",
            risk_level="medium",
            confidence_score="high",
            recommended_action="Complete DNS/email validation so the certificate can transition to ISSUED.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "acm_certificate_validation_issue":
        rtype = "acm_investigate_validation_failure"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Investigate ACM certificate validation failure",
            explanation="This ACM certificate is in a failed validation state and may break TLS dependencies.",
            risk_level="high",
            confidence_score="high",
            recommended_action="Review failure reason, correct validation inputs, and re-issue or replace the certificate.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "ses_identity_unverified":
        rtype = "ses_fix_identity_verification"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Verify SES identity",
            explanation="This SES identity is not fully verified and may not be eligible for reliable sending.",
            risk_level="medium",
            confidence_score="high",
            recommended_action="Complete SES identity verification (DNS/email) and re-check sending readiness.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "ses_sending_disabled_identity":
        rtype = "ses_review_sending_configuration"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Review SES identity sending configuration",
            explanation="This SES identity has sending disabled and should be reviewed for expected mail flow readiness.",
            risk_level="medium",
            confidence_score="high",
            recommended_action="Validate SES account/identity settings and re-enable sending if this identity should be active.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "security_group_world_open_sensitive_port":
        rtype = "security_group_restrict_world_open_ports"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Restrict world-open sensitive security group ports",
            explanation=(
                "This security group exposes sensitive ports to the public internet. "
                "Restrict ingress to known source CIDRs to reduce attack surface."
            ),
            risk_level="high",
            confidence_score="high",
            recommended_action="Remove 0.0.0.0/0 or ::/0 access for SSH/RDP/all-ports unless explicitly required.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "security_group_overly_permissive":
        rtype = "security_group_restrict_ingress"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Review and restrict broad security group ingress rules",
            explanation="This security group has a high count of ingress rules and should be reviewed for least privilege.",
            risk_level="medium",
            confidence_score="medium",
            recommended_action="Prune stale ingress rules and scope remaining access to required ports and CIDRs only.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "lambda_outdated_runtime":
        rtype = "lambda_update_runtime"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Update Lambda runtime from deprecated version",
            explanation="This Lambda function uses an outdated runtime that should be upgraded to a supported version.",
            risk_level="high",
            confidence_score="high",
            recommended_action="Test and deploy a supported runtime version for this function.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "lambda_review_timeout_configuration":
        rtype = "lambda_review_timeout_configuration"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Review Lambda timeout configuration",
            explanation="This Lambda function has a high timeout configuration and should be validated against execution patterns.",
            risk_level="medium",
            confidence_score="medium",
            recommended_action="Validate function timeout against actual duration and retry/error-handling behavior.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "rds_missing_required_tags":
        rtype = "rds_add_required_tags"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Add required tags to RDS resource",
            explanation="This resource is missing one or more required tags: Name, Environment.",
            risk_level="medium",
            confidence_score="high",
            recommended_action="Add Name and Environment tags to this resource.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "rds_publicly_accessible":
        rtype = "rds_disable_public_access"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        if finding.resource_type == "aurora_cluster":
            pub_summary = "Disable public accessibility on Aurora cluster"
            pub_explanation = (
                "This Aurora cluster has at least one publicly accessible instance and may be exposed to the internet."
            )
        else:
            pub_summary = "Disable public accessibility on RDS instance"
            pub_explanation = "This RDS instance is publicly accessible and may be exposed to the internet."
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary=pub_summary,
            explanation=pub_explanation,
            risk_level="high",
            confidence_score="high",
            recommended_action="Set publicly accessible to false and restrict network access.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "aurora_serverless_review_candidate":
        evidence = finding.evidence_json or {}
        publicly_accessible = evidence.get("publicly_accessible")
        if publicly_accessible is True:
            access_note = " The instance is publicly accessible, which increases exposure risk."
        elif publicly_accessible is False:
            access_note = " The instance is not publicly accessible."
        else:
            access_note = ""

        rtype = "aurora_serverless_cost_review"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        explanation = _aurora_serverless_explanation(finding) + access_note

        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Review Aurora Serverless configuration for cost efficiency",
            explanation=explanation,
            risk_level="medium",
            confidence_score="medium",
            recommended_action=(
                "Review scaling configuration and access patterns. "
                "Ensure capacity settings and public exposure are aligned with workload needs."
            ),
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "lambda_missing_required_tags":
        rtype = "lambda_add_required_tags"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Add required tags to Lambda function",
            explanation="This Lambda function is missing one or more required tags: Name, Environment.",
            risk_level="medium",
            confidence_score="high",
            recommended_action="Add Name and Environment tags to this Lambda function.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "lambda_high_memory_configuration_candidate":
        evidence = finding.evidence_json or {}
        memory_size = evidence.get("memory_size")
        memory_detail = f" Current memory_size is {memory_size} MB." if memory_size is not None else ""
        rtype = "lambda_rightsize_memory"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)

        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Review Lambda memory allocation for cost efficiency",
            explanation=(
                "This function is configured with high memory. Reducing memory size based on actual usage can lower "
                "cost without impacting performance."
                + memory_detail
            ),
            risk_level="medium",
            confidence_score="medium",
            recommended_action="Profile invocation performance and reduce memory_size where safe.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "s3_missing_required_tags":
        rtype = "s3_add_required_tags"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Add required tags to S3 bucket",
            explanation="This S3 bucket is missing one or more required tags: Name, Environment.",
            risk_level="medium",
            confidence_score="high",
            recommended_action="Add Name and Environment tags to this S3 bucket for governance and cost allocation.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "s3_public_access_candidate":
        rtype = "s3_enable_public_access_block"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Enable S3 Public Access Block",
            explanation="This bucket is not fully protected by S3 Public Access Block. Enable all four controls.",
            risk_level="high",
            confidence_score="high",
            recommended_action="Enable all S3 Public Access Block settings: block_public_acls, ignore_public_acls, block_public_policy, restrict_public_buckets.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "s3_versioning_disabled_candidate":
        rtype = "s3_enable_versioning"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Enable versioning on S3 bucket",
            explanation="This bucket does not have versioning enabled. Versioning protects against accidental deletion and provides recovery capability.",
            risk_level="medium",
            confidence_score="medium",
            recommended_action="Enable versioning on this S3 bucket to protect against accidental deletion and enable object recovery.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "s3_lifecycle_policy_missing":
        rtype = "s3_add_lifecycle_policy"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Add lifecycle policy to S3 bucket",
            explanation="This bucket does not have lifecycle rules configured. Lifecycle policies can reduce storage costs by automatically transitioning or deleting old objects.",
            risk_level="medium",
            confidence_score="medium",
            recommended_action="Add a lifecycle policy to this S3 bucket to auto-transition objects to cheaper storage classes or delete old versions after a set retention period.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "nat_gateway_cost_review_candidate":
        rtype = "nat_gateway_cost_review"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Review NAT Gateway usage for cost savings",
            explanation=_nat_gateway_explanation(finding),
            risk_level="medium",
            confidence_score="medium",
            recommended_action=(
                "Review NAT traffic sources, routing, and possible VPC endpoint alternatives "
                "to reduce NAT Gateway charges."
            ),
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "waf_cost_review_candidate":
        rtype = "waf_cost_review"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Review AWS WAF usage for cost savings",
            explanation=(
                "AWS WAF is contributing to your monthly cost. Review rule usage, request patterns, and logging "
                "settings to reduce unnecessary charges."
            ),
            risk_level="medium",
            confidence_score="medium",
            recommended_action=(
                "Review WAF rule count, request patterns, and logging settings to reduce WAF charges where possible."
            ),
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "target_group_no_healthy_targets":
        rtype = "target_group_review_target_health"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Review unhealthy target group targets",
            explanation=(
                "This target group has registered targets but none are currently healthy. "
                "Update target configuration, health check settings, or backend readiness to restore traffic."
            ),
            risk_level="high",
            confidence_score="high",
            recommended_action="Inspect target health reasons and fix service endpoints or security group rules.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "target_group_stickiness_disabled":
        rtype = "target_group_enable_stickiness"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Enable session stickiness on target group",
            explanation=(
                "This target group does not have session stickiness enabled. "
                "Enable stickiness if your application maintains stateful connections or user sessions."
            ),
            risk_level="low",
            confidence_score="medium",
            recommended_action="Enable stickiness (duration typically 1 hour) if targets maintain session state.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "target_group_slow_deregistration":
        rtype = "target_group_optimize_deregistration_delay"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Optimize target group deregistration delay",
            explanation=(
                "This target group has a long deregistration delay (>60 seconds). "
                "Reducing this can minimize traffic loss during deployments and scale-down events."
            ),
            risk_level="low",
            confidence_score="high",
            recommended_action="Reduce deregistration delay to 30-60 seconds after testing graceful shutdown behavior.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "rds_parameter_group_slow_query_disabled":
        rtype = "rds_parameter_group_enable_slow_query_log"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Enable slow query logging on RDS parameter group",
            explanation=(
                "This RDS parameter group does not have slow query logging enabled. "
                "Enabling slow_query_log provides visibility into query performance issues and optimization opportunities."
            ),
            risk_level="low",
            confidence_score="high",
            recommended_action="Enable slow_query_log parameter and set long_query_time threshold appropriately.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    if finding.finding_type == "rds_parameter_group_general_log_enabled":
        rtype = "rds_parameter_group_disable_general_log"
        rcat = _recommendation_category_for_type(rtype)
        sb, cr = _credibility_pair(rtype, rcat, estimated_savings)
        return Recommendation(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding_id=finding.id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            recommendation_type=rtype,
            recommendation_category=rcat,
            summary="Disable general query logging on RDS parameter group",
            explanation=(
                "This RDS parameter group has general query logging enabled. "
                "General logging logs all queries and can significantly impact database performance; use slow query log instead."
            ),
            risk_level="medium",
            confidence_score="high",
            recommended_action="Disable general_log parameter to improve database performance.",
            estimated_savings=estimated_savings,
            savings_basis=sb,
            confidence_reason=cr,
            created_at=created_at,
        )

    return None


_RECOMMENDATION_SOURCE_FINDING_TYPES = (
    "rds_missing_required_tags",
    "rds_publicly_accessible",
    "aurora_serverless_review_candidate",
    "lambda_missing_required_tags",
    "lambda_high_memory_configuration_candidate",
    "s3_missing_required_tags",
    "s3_public_access_candidate",
    "s3_versioning_disabled_candidate",
    "s3_lifecycle_policy_missing",
    "nat_gateway_cost_review_candidate",
    "waf_cost_review_candidate",
    *_GOVERNANCE_TAG_FINDING_TO_RECOMMENDATION.keys(),
    "ec2_stopped_instance",
    "acm_certificate_expiring_soon",
    "cloudfront_insecure_viewer_protocol_policy",
    "cloudfront_missing_https_redirect",
    "cloudfront_disabled_distribution_review",
    "apigateway_public_exposure_review",
    "eventbridge_rule_without_targets",
    "eventbridge_rule_disabled_review",
    "load_balancer_deletion_protection_disabled",
    "load_balancer_no_healthy_targets",
    "route_table_unassociated_review",
    "route_table_public_default_route_review",
    "acm_certificate_pending_validation",
    "acm_certificate_validation_issue",
    "ses_identity_unverified",
    "ses_sending_disabled_identity",
    "security_group_world_open_sensitive_port",
    "security_group_overly_permissive",
    "lambda_outdated_runtime",
    "lambda_review_timeout_configuration",
    "target_group_no_healthy_targets",
    "target_group_stickiness_disabled",
    "target_group_slow_deregistration",
    "rds_parameter_group_slow_query_disabled",
    "rds_parameter_group_general_log_enabled",
)


def generate_rds_recommendations(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    *,
    sync_run_id: UUID | None = None,
) -> RecommendationRunResult:
    """Generate recommendations from findings for one sync/detect run (or legacy latest-timestamp batch)."""
    _get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)

    created_at = utc_now()

    if sync_run_id is not None:
        run_id: UUID | None = sync_run_id
    else:
        anchor = (
            db_session.query(Finding)
            .filter(
                Finding.tenant_id == tenant_id,
                Finding.cloud_account_id == cloud_account_id,
            )
            .order_by(Finding.detected_at.desc())
            .first()
        )
        run_id = anchor.sync_run_id if anchor and anchor.sync_run_id is not None else None

    if run_id is not None:
        latest_findings = (
            db_session.query(Finding)
            .filter(
                Finding.tenant_id == tenant_id,
                Finding.cloud_account_id == cloud_account_id,
                Finding.sync_run_id == run_id,
                Finding.finding_type.in_(_RECOMMENDATION_SOURCE_FINDING_TYPES),
            )
            .order_by(Finding.resource_type.asc(), Finding.resource_id.asc(), Finding.finding_type.asc())
            .all()
        )
    else:
        latest_detected_at = (
            db_session.query(func.max(Finding.detected_at))
            .filter(
                Finding.tenant_id == tenant_id,
                Finding.cloud_account_id == cloud_account_id,
            )
            .scalar()
        )
        if latest_detected_at is None:
            return RecommendationRunResult(
                cloud_account_id=cloud_account_id,
                recommendations_created=0,
                created_at=created_at,
            )
        latest_findings = (
            db_session.query(Finding)
            .filter(
                Finding.tenant_id == tenant_id,
                Finding.cloud_account_id == cloud_account_id,
                Finding.detected_at == latest_detected_at,
                Finding.finding_type.in_(_RECOMMENDATION_SOURCE_FINDING_TYPES),
            )
            .order_by(Finding.resource_type.asc(), Finding.resource_id.asc(), Finding.finding_type.asc())
            .all()
        )

    if latest_findings:
        latest_findings = detection_service.deduplicate_aurora_serverless_review_findings(latest_findings)

    recommendations: list[Recommendation] = []
    for finding in latest_findings:
        recommendation = _build_recommendation_for_finding(
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            finding=finding,
            created_at=created_at,
        )
        if recommendation is not None:
            recommendations.append(recommendation)

    if recommendations:
        try:
            db_session.add_all(recommendations)
            db_session.commit()
        except Exception:
            db_session.rollback()
            logger.exception(
                "RDS recommendation generation commit failed",
                extra={
                    "tenant_id": str(tenant_id),
                    "cloud_account_id": str(cloud_account_id),
                    "recommendations_count": len(recommendations),
                },
            )
            raise

    return RecommendationRunResult(
        cloud_account_id=cloud_account_id,
        recommendations_created=len(recommendations),
        created_at=created_at,
    )


def generate_lambda_recommendations(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    *,
    sync_run_id: UUID | None = None,
) -> RecommendationRunResult:
    """Generate Lambda recommendations from latest findings for a cloud account."""
    return generate_rds_recommendations(
        db_session, tenant_id, cloud_account_id, sync_run_id=sync_run_id
    )


def generate_s3_recommendations(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    *,
    sync_run_id: UUID | None = None,
) -> RecommendationRunResult:
    """Generate S3 recommendations from latest findings for a cloud account."""
    return generate_rds_recommendations(
        db_session, tenant_id, cloud_account_id, sync_run_id=sync_run_id
    )


def list_recommendations(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    latest_only: bool = True,
) -> list[Recommendation]:
    """List recommendations for a tenant-scoped cloud account."""
    _get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)

    if not latest_only:
        return (
            db_session.query(Recommendation)
            .filter(
                Recommendation.tenant_id == tenant_id,
                Recommendation.cloud_account_id == cloud_account_id,
            )
            .order_by(Recommendation.created_at.desc(), Recommendation.id.desc())
            .all()
        )

    ranked_recommendations = (
        db_session.query(
            Recommendation.id.label("recommendation_id"),
            func.row_number()
            .over(
                partition_by=(Recommendation.resource_id, Recommendation.recommendation_type),
                order_by=(Recommendation.created_at.desc(), Recommendation.id.desc()),
            )
            .label("row_num"),
        )
        .filter(
            Recommendation.tenant_id == tenant_id,
            Recommendation.cloud_account_id == cloud_account_id,
        )
        .subquery()
    )

    rows = (
        db_session.query(Recommendation)
        .join(ranked_recommendations, Recommendation.id == ranked_recommendations.c.recommendation_id)
        .filter(ranked_recommendations.c.row_num == 1)
        .order_by(Recommendation.created_at.desc(), Recommendation.id.desc())
        .all()
    )
    return _dedupe_aurora_serverless_recommendations(rows)


def get_top_opportunities(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    limit: int = 5,
    exclude_applied: bool = False,
) -> list[TopOpportunity]:
    """Get top recommendations ranked by computed impact score."""
    _get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)

    recommendations = list_recommendations(
        db_session=db_session,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        latest_only=True,
    )
    if exclude_applied and recommendations:
        outcome_rows = (
            db_session.query(RecommendationOutcome)
            .filter(
                RecommendationOutcome.tenant_id == tenant_id,
                RecommendationOutcome.cloud_account_id == cloud_account_id,
            )
            .all()
        )
        applied_ids = {
            o.recommendation_id
            for o in outcome_rows
            if recommendation_outcome_service.normalized_workflow_status(o.workflow_status) in {"applied", "verified"}
        }
        recommendations = [r for r in recommendations if r.id not in applied_ids]

    ranks = rank_by_computed_score(recommendations)
    stats_by_type = recommendation_intelligence_service.get_recommendation_type_stats(
        db_session, tenant_id, cloud_account_id
    )

    savings_values = [float(r.estimated_savings) for r in recommendations if r.estimated_savings is not None]
    max_savings = max(savings_values) if savings_values else 0.0

    opportunities: list[TopOpportunity] = []
    findings_by_id = {
        f.id: f
        for f in (
            db_session.query(Finding)
            .filter(
                Finding.tenant_id == tenant_id,
                Finding.cloud_account_id == cloud_account_id,
            )
            .all()
        )
    }
    for rec in recommendations:
        factors = decision_factors_for(rec, max_savings)
        total_score = computed_impact_score(rec, max_savings)
        learned = recommendation_intelligence_service.learned_intelligence_for_recommendation(rec, stats_by_type)
        steps, estimated_time, difficulty = guided_actions_for_type(rec.recommendation_type)
        opportunities.append(
            TopOpportunity(
                recommendation_id=rec.id,
                resource_id=rec.resource_id,
                resource_type=rec.resource_type,
                recommendation_type=rec.recommendation_type,
                recommendation_category=rec.recommendation_category,
                summary=rec.summary,
                ai_explanation=(
                    generate_ai_explanation(
                        recommendation_type=rec.recommendation_type,
                        resource_id=rec.resource_id,
                        estimated_savings=rec.estimated_savings,
                        risk_level=rec.risk_level,
                        recommended_action=rec.recommended_action,
                        evidence_json=(findings_by_id.get(rec.finding_id).evidence_json if findings_by_id.get(rec.finding_id) else None),
                    )
                    or rec.explanation
                ),
                estimated_savings=(
                    round_currency(rec.estimated_savings) if rec.estimated_savings is not None else None
                ),
                risk_level=rec.risk_level,
                confidence_score=rec.confidence_score,
                computed_score=total_score,
                normalized_savings=factors["normalized_savings"],
                risk_factor=factors["risk_factor"],
                confidence_factor=factors["confidence_factor"],
                urgency_factor=factors["urgency_factor"],
                ranking_reason=ranking_reason_for(rec, factors),
                priority_bucket=priority_bucket_for(total_score),
                savings_basis=effective_savings_basis(rec),
                confidence_reason=effective_confidence_reason(rec),
                why_it_matters=why_it_matters_for(ranks.get(rec.id), rec.recommendation_category),
                learned_confidence=learned["learned_confidence"],
                learned_confidence_reason=learned["learned_confidence_reason"],
                historical_success_rate=learned["historical_success_rate"],
                avg_realized_savings_for_type=learned["avg_realized_savings_for_type"],
                steps=steps,
                estimated_time=estimated_time,
                difficulty=difficulty,
            )
        )

    opportunities.sort(key=lambda x: x.computed_score, reverse=True)
    return opportunities[:limit]


def list_recommendation_reads(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    latest_only: bool = True,
) -> list[RecommendationRead]:
    """List recommendations with rank-derived ``why_it_matters`` for API responses."""
    recs = list_recommendations(
        db_session=db_session,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        latest_only=latest_only,
    )
    ranks = rank_by_computed_score(recs)
    stats_by_type = recommendation_intelligence_service.get_recommendation_type_stats(
        db_session, tenant_id, cloud_account_id
    )
    outcome_rows = (
        db_session.query(RecommendationOutcome)
        .filter(
            RecommendationOutcome.tenant_id == tenant_id,
            RecommendationOutcome.cloud_account_id == cloud_account_id,
        )
        .all()
    )
    outcome_by_recommendation_id = {o.recommendation_id: o for o in outcome_rows}
    findings_by_id = {
        f.id: f
        for f in (
            db_session.query(Finding)
            .filter(
                Finding.tenant_id == tenant_id,
                Finding.cloud_account_id == cloud_account_id,
            )
            .all()
        )
    }
    return [
        _enrich_recommendation_read(
            recommendation_read_from_orm(
                r,
                ranks.get(r.id),
                evidence_json=(findings_by_id.get(r.finding_id).evidence_json if findings_by_id.get(r.finding_id) else None),
            ),
            r,
            stats_by_type,
            outcome_by_recommendation_id=outcome_by_recommendation_id,
        )
        for r in recs
    ]


def _aurora_serverless_cluster_key_for_recommendation(rec: Recommendation) -> str:
    """Align writer instance and cluster rows to one logical Aurora cluster (see detection dedupe)."""
    if rec.recommendation_type != "aurora_serverless_cost_review":
        return rec.resource_id
    if rec.resource_type == "aurora_cluster":
        return rec.resource_id
    rid = rec.resource_id or ""
    return re.sub(r"-instance-\d+$", "", rid) or rid


def _dedupe_aurora_serverless_recommendations(recs: list[Recommendation]) -> list[Recommendation]:
    """Drop duplicate Aurora Serverless rows that target the same cluster (instance + cluster)."""
    by_key: dict[str, list[Recommendation]] = {}
    for r in recs:
        if r.recommendation_type != "aurora_serverless_cost_review":
            continue
        k = _aurora_serverless_cluster_key_for_recommendation(r)
        by_key.setdefault(k, []).append(r)

    drop: set[UUID] = set()
    for group in by_key.values():
        if len(group) <= 1:
            continue
        clusters = [x for x in group if x.resource_type == "aurora_cluster"]
        pool = clusters if clusters else group
        winner = max(
            pool,
            key=lambda x: (
                float(x.estimated_savings or 0),
                x.created_at.timestamp() if x.created_at else 0.0,
            ),
        )
        for x in group:
            if x.id != winner.id:
                drop.add(x.id)

    return [r for r in recs if r.id not in drop]


def get_recommendation_guide_by_id(
    db_session: Session,
    recommendation_id: UUID,
) -> dict:
    rec = db_session.query(Recommendation).filter(Recommendation.id == recommendation_id).first()
    if rec is None:
        raise ValueError("recommendation_not_found")
    steps, estimated_time, difficulty = guided_actions_for_type(rec.recommendation_type)
    return {
        "steps": steps,
        "estimated_time": estimated_time,
        "difficulty": difficulty,
    }


def get_action_plan(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    limit: int = 3,
) -> list[dict]:
    """
    Return a short prioritized plan (top 3-5) based on existing recommendation data.
    Sorting:
      1) computed_score desc
      2) estimated_savings desc
      3) risk_level asc (prefer lower risk for equal impact)
    Excludes applied/verified workflow outcomes.
    """
    _get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)
    cap = max(3, min(limit, 5))

    recommendations = list_recommendations(
        db_session=db_session,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        latest_only=True,
    )
    outcome_rows = (
        db_session.query(RecommendationOutcome)
        .filter(
            RecommendationOutcome.tenant_id == tenant_id,
            RecommendationOutcome.cloud_account_id == cloud_account_id,
        )
        .all()
    )
    excluded_ids = {
        o.recommendation_id
        for o in outcome_rows
        if recommendation_outcome_service.normalized_workflow_status(o.workflow_status) in {"applied", "verified"}
    }
    candidates = [r for r in recommendations if r.id not in excluded_ids]
    if not candidates:
        return []

    findings_by_id = {
        f.id: f
        for f in (
            db_session.query(Finding)
            .filter(
                Finding.tenant_id == tenant_id,
                Finding.cloud_account_id == cloud_account_id,
            )
            .all()
        )
    }
    savings_values = [float(r.estimated_savings) for r in candidates if r.estimated_savings is not None]
    max_savings = max(savings_values) if savings_values else 0.0
    risk_rank = {"low": 0, "medium": 1, "high": 2}

    rows: list[tuple[Recommendation, float, float, int, str, str]] = []
    for rec in candidates:
        score = computed_impact_score(rec, max_savings)
        savings = round_currency(rec.estimated_savings) if rec.estimated_savings is not None else 0.0
        rr = risk_rank.get((rec.risk_level or "").lower(), 1)
        factors = decision_factors_for(rec, max_savings)
        reason = ranking_reason_for(rec, factors)
        ai = generate_ai_explanation(
            recommendation_type=rec.recommendation_type,
            resource_id=rec.resource_id,
            estimated_savings=rec.estimated_savings,
            risk_level=rec.risk_level,
            recommended_action=rec.recommended_action,
            evidence_json=(findings_by_id.get(rec.finding_id).evidence_json if findings_by_id.get(rec.finding_id) else None),
        )
        expected_impact = (
            f"Potential savings around ${savings:,.2f}/month with {rec.risk_level} implementation risk."
            if rec.estimated_savings is not None
            else f"Risk reduction with {rec.risk_level} implementation risk and no direct savings estimate."
        )
        # Keep reason concise (1-2 sentences)
        concise_reason = (ai or reason).split(". ")
        concise_reason = ". ".join(concise_reason[:2]).strip()
        if concise_reason and not concise_reason.endswith("."):
            concise_reason += "."
        rows.append((rec, score, savings, rr, concise_reason or reason, expected_impact))

    rows.sort(key=lambda x: (-x[1], -x[2], x[3]))
    top = rows[:cap]
    items: list[dict] = []
    for idx, (rec, _score, _savings, _rr, reason, impact) in enumerate(top, start=1):
        items.append(
            {
                "step_number": idx,
                "recommendation_id": rec.id,
                "recommendation_type": rec.recommendation_type,
                "resource_id": rec.resource_id,
                "resource_type": rec.resource_type,
                "summary": rec.summary,
                "estimated_savings": (
                    round_currency(rec.estimated_savings) if rec.estimated_savings is not None else None
                ),
                "risk_level": rec.risk_level,
                "reason": reason,
                "expected_impact": impact,
            }
        )
    return items
