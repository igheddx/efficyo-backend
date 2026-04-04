from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.finding import Finding
from app.models.recommendation import Recommendation
from app.models.recommendation_outcome import RecommendationOutcome
from app.models.sync_pipeline import SyncTask
from app.services import detection_extended_service, detection_service, recommendation_service
from app.rules import run_rule_engine
from app.sync import repository


def analyzer_bundle(db: Session, task: SyncTask) -> dict[str, Any]:
    """
    Deterministic analysis: detection (findings) for supported resource types, then recommendations.

    For idempotent retries, prior findings/recommendations for ``sync_run_id == sync_job_id`` are removed first.
    """

    cloud_account_id: UUID | None = task.scope_id
    if cloud_account_id is None:
        return {"analyzer": "bundle", "status": "skipped", "reason": "missing_scope_id"}

    resolved = repository.resolve_cloud_account_org_tenant(db, cloud_account_id)
    if resolved is None:
        return {"analyzer": "bundle", "status": "failed", "error": "cloud_account_not_found"}

    tenant_id, _org_id = resolved
    sync_run_id = task.sync_job_id

    existing_finding_ids = [
        f.id
        for f in (
            db.query(Finding.id)
            .filter(Finding.tenant_id == tenant_id)
            .filter(Finding.cloud_account_id == cloud_account_id)
            .filter(Finding.sync_run_id == sync_run_id)
            .all()
        )
    ]
    if existing_finding_ids:
        db.query(RecommendationOutcome).filter(
            RecommendationOutcome.tenant_id == tenant_id,
            RecommendationOutcome.cloud_account_id == cloud_account_id,
            RecommendationOutcome.recommendation_id.in_(
                db.query(Recommendation.id).filter(
                    Recommendation.tenant_id == tenant_id,
                    Recommendation.cloud_account_id == cloud_account_id,
                    Recommendation.finding_id.in_(existing_finding_ids),
                ).subquery()
            ),
        ).delete(synchronize_session=False)
        db.query(Recommendation).filter(
            Recommendation.tenant_id == tenant_id,
            Recommendation.cloud_account_id == cloud_account_id,
            Recommendation.finding_id.in_(existing_finding_ids),
        ).delete(synchronize_session=False)
        db.query(Finding).filter(
            Finding.tenant_id == tenant_id,
            Finding.cloud_account_id == cloud_account_id,
            Finding.sync_run_id == sync_run_id,
        ).delete(synchronize_session=False)
        db.flush()

    r_ec2 = detection_service.detect_ec2_findings(db, tenant_id, cloud_account_id, sync_run_id)
    r_ebs = detection_service.detect_ebs_findings(db, tenant_id, cloud_account_id, sync_run_id)
    r_rds = detection_service.detect_rds_findings(db, tenant_id, cloud_account_id, sync_run_id)
    r_lambda = detection_service.detect_lambda_findings(db, tenant_id, cloud_account_id, sync_run_id)
    r_s3 = detection_service.detect_s3_findings(db, tenant_id, cloud_account_id, sync_run_id)
    r_ext = detection_extended_service.detect_extended_findings(db, tenant_id, cloud_account_id, sync_run_id)
    r_rules = run_rule_engine(db, tenant_id, cloud_account_id, sync_run_id)

    rec_run = recommendation_service.generate_rds_recommendations(
        db, tenant_id, cloud_account_id, sync_run_id=sync_run_id
    )

    return {
        "analyzer": "bundle",
        "status": "ok",
        "cloud_account_id": str(cloud_account_id),
        "findings_created": {
            "ec2": getattr(r_ec2, "findings_created", None),
            "ebs": getattr(r_ebs, "findings_created", None),
            "rds": getattr(r_rds, "findings_created", None),
            "lambda": getattr(r_lambda, "findings_created", None),
            "s3": getattr(r_s3, "findings_created", None),
            "extended": getattr(r_ext, "findings_created", None),
            "rule_engine": getattr(r_rules, "findings_created", None),
        },
        "recommendations_created": getattr(rec_run, "recommendations_created", None),
    }
