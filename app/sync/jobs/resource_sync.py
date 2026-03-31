from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.services import detection_service, ingestion_service, recommendation_service


class ResourceSyncJobRunner:
    def run(self, db: Session, *, tenant_id: UUID, cloud_account_id: UUID, sync_run_id: UUID) -> dict:
        ingestion_service.ingest_ec2(db, tenant_id, cloud_account_id)
        ingestion_service.ingest_ebs(db, tenant_id, cloud_account_id)
        ingestion_service.ingest_rds(db, tenant_id, cloud_account_id)
        ingestion_service.ingest_lambda(db, tenant_id, cloud_account_id)
        ingestion_service.ingest_s3(db, tenant_id, cloud_account_id)

        detection_service.detect_ec2_findings(db, tenant_id, cloud_account_id, sync_run_id)
        detection_service.detect_ebs_findings(db, tenant_id, cloud_account_id, sync_run_id)
        detection_service.detect_rds_findings(db, tenant_id, cloud_account_id, sync_run_id)
        detection_service.detect_lambda_findings(db, tenant_id, cloud_account_id, sync_run_id)
        detection_service.detect_s3_findings(db, tenant_id, cloud_account_id, sync_run_id)

        recommendation_service.generate_rds_recommendations(db, tenant_id, cloud_account_id, sync_run_id=sync_run_id)
        return {"status": "ok"}


def run_resource_sync(db: Session, *, tenant_id: UUID, cloud_account_id: UUID, sync_run_id: UUID) -> dict:
    return ResourceSyncJobRunner().run(
        db,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        sync_run_id=sync_run_id,
    )

