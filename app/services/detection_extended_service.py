"""Extended governance/security findings for CloudFront, ACM, API Gateway, EventBridge, SES, and VPC networking."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.db import utc_now
from app.models.finding import Finding
from app.models.resource_snapshot import ResourceSnapshot
from app.services import cloud_account_service
from app.services.detection_service import DetectionRunResult, _missing_required_tags


def _latest_snapshots(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    resource_type: str,
) -> list[ResourceSnapshot]:
    latest_captured = (
        db_session.query(func.max(ResourceSnapshot.captured_at))
        .filter(
            ResourceSnapshot.tenant_id == tenant_id,
            ResourceSnapshot.cloud_account_id == cloud_account_id,
            ResourceSnapshot.resource_type == resource_type,
        )
        .scalar()
    )
    if latest_captured is None:
        return []
    return (
        db_session.query(ResourceSnapshot)
        .filter(
            ResourceSnapshot.tenant_id == tenant_id,
            ResourceSnapshot.cloud_account_id == cloud_account_id,
            ResourceSnapshot.captured_at == latest_captured,
            ResourceSnapshot.resource_type == resource_type,
        )
        .order_by(ResourceSnapshot.resource_id.asc())
        .all()
    )


def detect_extended_findings(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    sync_run_id: UUID,
) -> DetectionRunResult:
    """Governance (required tags) and ACM expiry signals for extended inventory types."""
    cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)
    detected_at = utc_now()
    findings: list[Finding] = []

    tagged_types = [
        "cloudfront_distribution",
        "acm_certificate",
        "apigateway_rest_api",
        "apigateway_http_api",
        "eventbridge_rule",
        "ses_email_identity",
        "vpc",
        "subnet",
        "nat_gateway",
        "internet_gateway",
        "security_group",
    ]

    for rt in tagged_types:
        for snapshot in _latest_snapshots(db_session, tenant_id, cloud_account_id, rt):
            tags = snapshot.tags_json or {}
            missing = _missing_required_tags(tags)
            if missing:
                findings.append(
                    Finding(
                        tenant_id=tenant_id,
                        cloud_account_id=cloud_account_id,
                        resource_snapshot_id=snapshot.id,
                        resource_id=snapshot.resource_id,
                        resource_type=snapshot.resource_type,
                        finding_type=f"{rt}_missing_required_tags",
                        severity="medium",
                        evidence_json={"missing_tags": missing, "tags": tags},
                        detected_at=detected_at,
                        sync_run_id=sync_run_id,
                    )
                )

    for snapshot in _latest_snapshots(db_session, tenant_id, cloud_account_id, "acm_certificate"):
        cfg = snapshot.configuration_json or {}
        na = cfg.get("not_after")
        if not na or not isinstance(na, str):
            continue
        try:
            exp = datetime.fromisoformat(na.replace("Z", "+00:00"))
        except ValueError:
            continue
        now = datetime.now(timezone.utc)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        days = (exp - now).days
        if 0 < days <= 30:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="acm_certificate_expiring_soon",
                    severity="high",
                    evidence_json={"not_after": na, "days_remaining": days},
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

    if findings:
        db_session.add_all(findings)
        db_session.commit()

    return DetectionRunResult(
        cloud_account_id=cloud_account_id,
        resource_type="extended",
        findings_created=len(findings),
        detected_at=detected_at,
        sync_run_id=sync_run_id,
    )
