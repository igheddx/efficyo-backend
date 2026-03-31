from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.services import detection_service, recommendation_service


def run_cost_analyzer(
    db: Session,
    *,
    tenant_id: UUID,
    cloud_account_id: UUID,
    sync_run_id: UUID,
) -> dict[str, Any]:
    """
    Phase-1 scaffold:
    - consumes ResourceSnapshots (via existing detection_service)
    - creates findings + recommendation records (via existing recommendation_service)
    """

    detection_service.detect_ec2_findings(db, tenant_id, cloud_account_id, sync_run_id)
    detection_service.detect_ebs_findings(db, tenant_id, cloud_account_id, sync_run_id)
    detection_service.detect_rds_findings(db, tenant_id, cloud_account_id, sync_run_id)
    detection_service.detect_lambda_findings(db, tenant_id, cloud_account_id, sync_run_id)
    detection_service.detect_s3_findings(db, tenant_id, cloud_account_id, sync_run_id)

    rec_result = recommendation_service.generate_rds_recommendations(
        db, tenant_id, cloud_account_id, sync_run_id=sync_run_id
    )
    return {"analyzer": "cost", "recommendations_created": rec_result.recommendations_created}

