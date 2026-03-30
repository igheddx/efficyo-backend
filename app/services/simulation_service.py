"""Simulation service for recommendation what-if responses."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.cost_window import round_currency
from app.models.finding import Finding
from app.models.recommendation import Recommendation
from app.services import cloud_account_service


@dataclass
class SimulationResult:
    recommendation_id: UUID
    resource_id: str
    recommendation_type: str
    recommendation_category: str
    current_state: dict
    proposed_state: dict
    impact_summary: str
    risk_reduction: str
    estimated_savings: float | None
    confidence_score: str


def simulate_recommendation(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
) -> SimulationResult:
    """Build deterministic recommendation simulation without applying changes."""
    cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)

    recommendation = (
        db_session.query(Recommendation)
        .filter(
            Recommendation.tenant_id == tenant_id,
            Recommendation.cloud_account_id == cloud_account_id,
            Recommendation.id == recommendation_id,
        )
        .first()
    )
    if recommendation is None:
        raise ValueError("recommendation_not_found")

    finding = (
        db_session.query(Finding)
        .filter(
            Finding.id == recommendation.finding_id,
            Finding.tenant_id == tenant_id,
            Finding.cloud_account_id == cloud_account_id,
        )
        .first()
    )
    evidence = finding.evidence_json if finding is not None else {}

    current_state: dict
    proposed_state: dict
    impact_summary: str
    risk_reduction: str

    if recommendation.recommendation_type == "rds_disable_public_access":
        current_state = {"publicly_accessible": True}
        proposed_state = {"publicly_accessible": False}
        impact_summary = "Database would no longer be accessible from the public internet."
        risk_reduction = "high"
    elif recommendation.recommendation_type == "rds_add_required_tags":
        missing_tags = evidence.get("missing_tags") if isinstance(evidence, dict) else None
        if isinstance(missing_tags, list):
            current_state = {"missing_tags": missing_tags}
        else:
            current_state = {}
        proposed_state = {"Name": "<required>", "Environment": "<required>"}
        impact_summary = "Resource ownership, environment classification, and cost visibility would improve."
        risk_reduction = "medium"
    elif recommendation.recommendation_type == "aurora_serverless_cost_review":
        db_instance_class = evidence.get("db_instance_class") if isinstance(evidence, dict) else None
        if db_instance_class:
            current_state = {"db_instance_class": db_instance_class}
        else:
            current_state = {}
        proposed_state = {"action": "review_serverless_scaling_configuration"}
        impact_summary = (
            "Aurora Serverless capacity and access pattern review may identify opportunities to reduce monthly spend."
        )
        risk_reduction = "medium"
    elif recommendation.recommendation_type == "lambda_rightsize_memory":
        memory_size = evidence.get("memory_size") if isinstance(evidence, dict) else None
        if memory_size is not None:
            current_state = {"memory_size": memory_size}
        else:
            current_state = {}
        proposed_state = {"action": "reduce_lambda_memory_size_after_profiling"}
        impact_summary = "Lambda memory rightsizing may reduce monthly compute spend while maintaining performance."
        risk_reduction = "medium"
    elif recommendation.recommendation_type == "s3_add_required_tags":
        missing_tags = evidence.get("missing_tags") if isinstance(evidence, dict) else None
        if isinstance(missing_tags, list):
            current_state = {"missing_tags": missing_tags}
        else:
            current_state = {}
        proposed_state = {"Name": "<required>", "Environment": "<required>"}
        impact_summary = "S3 bucket governance, environment classification, and cost allocation would improve."
        risk_reduction = "medium"
    elif recommendation.recommendation_type == "s3_enable_public_access_block":
        pab_status = evidence.get("public_access_block_status") if isinstance(evidence, dict) else {}
        if isinstance(pab_status, dict):
            current_state = {"public_access_block_status": pab_status}
        else:
            current_state = {}
        proposed_state = {
            "block_public_acls": True,
            "ignore_public_acls": True,
            "block_public_policy": True,
            "restrict_public_buckets": True,
        }
        impact_summary = "S3 bucket would be protected against public data exposure."
        risk_reduction = "high"
    elif recommendation.recommendation_type == "s3_enable_versioning":
        versioning_status = evidence.get("versioning_status") if isinstance(evidence, dict) else None
        if versioning_status:
            current_state = {"versioning_status": versioning_status}
        else:
            current_state = {}
        proposed_state = {"versioning_status": "Enabled"}
        impact_summary = "S3 bucket would be protected against accidental deletion and enable object recovery."
        risk_reduction = "medium"
    elif recommendation.recommendation_type == "s3_add_lifecycle_policy":
        lifecycle_rules_count = evidence.get("lifecycle_rules_count") if isinstance(evidence, dict) else 0
        current_state = {"lifecycle_rules_count": lifecycle_rules_count}
        proposed_state = {"action": "create_lifecycle_policy_for_cost_optimization"}
        impact_summary = "S3 bucket lifecycle policy would automatically transition or delete old objects to reduce storage costs."
        risk_reduction = "medium"
    else:
        current_state = {}
        proposed_state = {}
        impact_summary = "No deterministic simulation is available for this recommendation type."
        risk_reduction = "low"

    estimated_savings = (
        round_currency(recommendation.estimated_savings)
        if recommendation.estimated_savings is not None
        else None
    )

    return SimulationResult(
        recommendation_id=recommendation.id,
        resource_id=recommendation.resource_id,
        recommendation_type=recommendation.recommendation_type,
        recommendation_category=recommendation.recommendation_category,
        current_state=current_state,
        proposed_state=proposed_state,
        impact_summary=impact_summary,
        risk_reduction=risk_reduction,
        estimated_savings=estimated_savings,
        confidence_score=recommendation.confidence_score,
    )
