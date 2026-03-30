"""Controlled execution layer: deterministic template-based script generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.recommendation import Recommendation


@dataclass(frozen=True)
class ExecutionTemplate:
    """Reusable action template with deterministic text blocks."""

    action_type: str
    cli_template: str
    terraform_template: str | None
    notes_template: str
    risk_template: str
    rollback_template: str


def _get_recommendation_or_raise(db_session: Session, recommendation_id: UUID) -> Recommendation:
    rec = db_session.query(Recommendation).filter(Recommendation.id == recommendation_id).first()
    if rec is None:
        raise ValueError("recommendation_not_found")
    return rec


# recommendation_type -> action_type mapping
_RECOMMENDATION_ACTION_MAP: dict[str, str] = {
    "s3_enable_public_access_block": "s3_enable_public_access_block",
    "rds_disable_public_access": "rds_disable_public_access",
    "lambda_rightsize_memory": "lambda_update_memory",
    "nat_gateway_cost_review": "nat_gateway_guided_optimization",
    "aurora_serverless_cost_review": "aurora_scaling_review",
}


def _default_params(rec: Recommendation) -> dict[str, str]:
    """Base params available to all templates."""
    return {
        "recommendation_type": rec.recommendation_type or "unknown",
        "resource_id": rec.resource_id or "<resource_id>",
        "resource_type": rec.resource_type or "<resource_type>",
        "bucket_name": rec.resource_id or "<bucket_name>",
        "db_instance_id": rec.resource_id or "<db_instance_id>",
        "function_name": rec.resource_id or "<function_name>",
        "cluster_id": rec.resource_id or "<cluster_id>",
        "old_value": "<old_value>",
        "new_value": "<new_value>",
        "region": "<region>",
        "vpc_id": "<vpc_id>",
        "route_table_ids": "<rtb-1> <rtb-2>",
        "route_table_id": "<rtb_id>",
        "prefix_list_id": "<pl-id>",
        "vpce_id": "<vpce-id>",
        "service_name": "com.amazonaws.<region>.s3",
    }


def _resolve_s3_params(rec: Recommendation) -> dict[str, str]:
    p = _default_params(rec)
    p["bucket_name"] = rec.resource_id or "<bucket_name>"
    return p


def _resolve_rds_public_params(rec: Recommendation) -> dict[str, str]:
    p = _default_params(rec)
    p["db_instance_id"] = rec.resource_id or "<db_instance_id>"
    return p


def _resolve_lambda_params(rec: Recommendation) -> dict[str, str]:
    p = _default_params(rec)
    p["function_name"] = rec.resource_id or "<function_name>"
    return p


def _resolve_aurora_params(rec: Recommendation) -> dict[str, str]:
    p = _default_params(rec)
    # Keep guided placeholders where we cannot safely infer exact values.
    p["cluster_id"] = rec.resource_id or "<cluster_id>"
    return p


def _resolve_nat_params(rec: Recommendation) -> dict[str, str]:
    p = _default_params(rec)
    # Guided optimization requires environment-specific values.
    return p


_PARAM_RESOLVERS: dict[str, Callable[[Recommendation], dict[str, str]]] = {
    "s3_enable_public_access_block": _resolve_s3_params,
    "s3_add_required_tags": _resolve_s3_params,
    "rds_disable_public_access": _resolve_rds_public_params,
    "lambda_update_memory": _resolve_lambda_params,
    "nat_gateway_guided_optimization": _resolve_nat_params,
    "aurora_scaling_review": _resolve_aurora_params,
}


_EXECUTION_TEMPLATE_REGISTRY: dict[str, ExecutionTemplate] = {
    "s3_enable_public_access_block": ExecutionTemplate(
        action_type="s3_enable_public_access_block",
        cli_template=(
            "aws s3api put-public-access-block \\\n"
            "  --bucket {bucket_name} \\\n"
            "  --public-access-block-configuration \\\n"
            "  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
        ),
        terraform_template=(
            'resource "aws_s3_bucket_public_access_block" "example" {\n'
            '  bucket = "{bucket_name}"\n\n'
            "  block_public_acls       = true\n"
            "  ignore_public_acls      = true\n"
            "  block_public_policy     = true\n"
            "  restrict_public_buckets = true\n"
            "}"
        ),
        notes_template="Run against bucket {bucket_name}. Validate bucket policy behavior before applying.",
        risk_template="Public website or externally required object access may stop working.",
        rollback_template=(
            "Set one or more Public Access Block flags to false for bucket {bucket_name} "
            "using aws s3api put-public-access-block."
        ),
    ),
    "s3_add_required_tags": ExecutionTemplate(
        action_type="s3_add_required_tags",
        cli_template=(
            "aws s3api put-bucket-tagging \\\n"
            "  --bucket {bucket_name} \\\n"
            "  --tagging 'TagSet=[{{Key=Environment,Value=<env>}},{{Key=Owner,Value=<owner>}}]'"
        ),
        terraform_template=(
            'resource "aws_s3_bucket_tagging" "example" {\n'
            '  bucket = "{bucket_name}"\n'
            "  tag_set = {\n"
            '    Environment = "<env>"\n'
            '    Owner       = "<owner>"\n'
            "  }\n"
            "}"
        ),
        notes_template="Choose required organizational tags before applying to {bucket_name}.",
        risk_template="Incorrect tags can break cost allocation reports and governance automation.",
        rollback_template="Re-apply prior tag set or remove incorrect tags with put-bucket-tagging.",
    ),
    "rds_disable_public_access": ExecutionTemplate(
        action_type="rds_disable_public_access",
        cli_template=(
            "aws rds modify-db-instance \\\n"
            "  --db-instance-identifier {db_instance_id} \\\n"
            "  --no-publicly-accessible \\\n"
            "  --apply-immediately"
        ),
        terraform_template=None,
        notes_template="Applies to DB instance {db_instance_id}. Validate application connectivity path first.",
        risk_template="If public access is still required, external clients may lose connectivity.",
        rollback_template=(
            "Re-enable with aws rds modify-db-instance --db-instance-identifier {db_instance_id} "
            "--publicly-accessible --apply-immediately."
        ),
    ),
    "lambda_update_memory": ExecutionTemplate(
        action_type="lambda_update_memory",
        cli_template=(
            "aws lambda update-function-configuration \\\n"
            "  --function-name {function_name} \\\n"
            "  --memory-size {new_value}"
        ),
        terraform_template=None,
        notes_template="Set {new_value} in MB after validating function performance and latency targets.",
        risk_template="Too-low memory can increase execution duration, timeouts, or retries.",
        rollback_template=(
            "Restore with aws lambda update-function-configuration --function-name {function_name} "
            "--memory-size {old_value}."
        ),
    ),
    "nat_gateway_guided_optimization": ExecutionTemplate(
        action_type="nat_gateway_guided_optimization",
        cli_template=(
            "# Example: create VPC endpoint to reduce NAT egress for S3 traffic\n"
            "aws ec2 create-vpc-endpoint \\\n"
            "  --vpc-id {vpc_id} \\\n"
            "  --service-name {service_name} \\\n"
            "  --route-table-ids {route_table_ids}\n\n"
            "# Update routes so eligible traffic bypasses NAT\n"
            "aws ec2 replace-route \\\n"
            "  --route-table-id {route_table_id} \\\n"
            "  --destination-prefix-list-id {prefix_list_id} \\\n"
            "  --vpc-endpoint-id {vpce_id}"
        ),
        terraform_template=None,
        notes_template=(
            "Guided template only: provide VPC, route-table IDs, prefix-list ID, and endpoint values per environment."
        ),
        risk_template="Incorrect route updates can break outbound paths for workloads.",
        rollback_template="Restore prior route table entries and remove unused endpoints if required.",
    ),
    "aurora_scaling_review": ExecutionTemplate(
        action_type="aurora_scaling_review",
        cli_template=(
            "# Aurora scaling is workload-specific. Validate values before applying.\n"
            "aws rds modify-db-cluster \\\n"
            "  --db-cluster-identifier {cluster_id} \\\n"
            "  --serverless-v2-scaling-configuration MinCapacity=<min>,MaxCapacity=<max> \\\n"
            "  --apply-immediately"
        ),
        terraform_template=None,
        notes_template=(
            "Guided template for {cluster_id}. Review ACU patterns, CPU, and connections before lowering capacity."
        ),
        risk_template="Aggressive scaling limits may degrade performance during peak load.",
        rollback_template="Revert MinCapacity/MaxCapacity to prior values with modify-db-cluster.",
    ),
}


_FALLBACK_TEMPLATE = ExecutionTemplate(
    action_type="generic_review",
    cli_template=(
        "# Manual remediation for recommendation type: {recommendation_type}\n"
        "# Review resource {resource_id} ({resource_type}) and apply the documented change carefully."
    ),
    terraform_template=None,
    notes_template="No action template exists yet. Apply through approved manual process with peer review.",
    risk_template="Configuration drift or unintended impact if changed without validation.",
    rollback_template="Capture current configuration first, then revert to the previous known-good settings.",
)


def _render_template(template: str | None, params: dict[str, str]) -> str | None:
    if template is None:
        return None
    return template.format_map(params)


def generate_execution_plan(db_session: Session, recommendation_id: UUID) -> dict:
    rec = _get_recommendation_or_raise(db_session, recommendation_id)
    rtype = (rec.recommendation_type or "").lower()
    action_type = _RECOMMENDATION_ACTION_MAP.get(rtype)
    template = _EXECUTION_TEMPLATE_REGISTRY.get(action_type or "", _FALLBACK_TEMPLATE)

    resolver = _PARAM_RESOLVERS.get(template.action_type, _default_params)
    params = resolver(rec)

    return {
        "cli_command": _render_template(template.cli_template, params) or "",
        "terraform_snippet": _render_template(template.terraform_template, params),
        "notes": _render_template(template.notes_template, params) or "",
        "risk": _render_template(template.risk_template, params) or "",
        "rollback": _render_template(template.rollback_template, params) or "",
    }

