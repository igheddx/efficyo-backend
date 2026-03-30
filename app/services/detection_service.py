"""Detection service for RDS and Aurora findings."""

from dataclasses import dataclass
from datetime import datetime
import logging
import re
from uuid import UUID

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.core.db import utc_now
from app.models.finding import Finding
from app.models.resource_snapshot import ResourceSnapshot
from app.services import cloud_account_service, cost_explorer_service
from app.services.pricing_service import estimate_monthly_savings_for_finding


logger = logging.getLogger(__name__)


OVERPROVISIONED_RDS_INSTANCE_TYPES = {
    "db.t3.medium",
    "db.t3.large",
    "db.t4g.medium",
    "db.t4g.large",
    "db.m5.large",
    "db.m5.xlarge",
    "db.r5.large",
    "db.r5.xlarge",
}


@dataclass
class DetectionRunResult:
    """Result of a detection run."""

    cloud_account_id: UUID
    resource_type: str
    findings_created: int
    detected_at: datetime
    sync_run_id: UUID


def _missing_required_tags(tags: dict) -> list[str]:
    return [tag for tag in ("Name", "Environment") if not tags.get(tag)]


def _aurora_serverless_cluster_key(finding: Finding) -> str:
    """
    Map instance + cluster Aurora Serverless findings to one logical cluster id.

    Writer instances use DBInstanceIdentifier like ``mycluster-instance-1``; clusters use
    ``DBClusterIdentifier`` (e.g. ``mycluster``).
    """
    if finding.resource_type == "aurora_cluster":
        return finding.resource_id
    rid = finding.resource_id or ""
    return re.sub(r"-instance-\d+$", "", rid) or rid


def _pick_preferred_aurora_serverless_finding(group: list[Finding]) -> Finding:
    """
    Prefer cluster-level finding over writer instance; then newest detection, savings, stable id.

    Used both when emitting a single run's findings and when collapsing duplicates for list/API.
    """
    clusters = [f for f in group if f.resource_type == "aurora_cluster"]
    pool = clusters if clusters else group
    return max(
        pool,
        key=lambda f: (
            f.detected_at.timestamp() if f.detected_at else 0.0,
            float(f.estimated_savings or 0),
            str(f.id),
        ),
    )


def _dedupe_aurora_serverless_review_findings(findings: list[Finding]) -> list[Finding]:
    """Keep one aurora_serverless_review_candidate per logical cluster."""
    other: list[Finding] = []
    serverless: list[Finding] = []
    for f in findings:
        if f.finding_type == "aurora_serverless_review_candidate":
            serverless.append(f)
        else:
            other.append(f)
    if len(serverless) <= 1:
        return findings

    groups: dict[str, list[Finding]] = {}
    for f in serverless:
        k = _aurora_serverless_cluster_key(f)
        groups.setdefault(k, []).append(f)

    kept = [_pick_preferred_aurora_serverless_finding(g) for g in groups.values()]
    return other + kept


def deduplicate_aurora_serverless_review_findings(findings: list[Finding]) -> list[Finding]:
    """Collapse duplicate Aurora Serverless findings (writer + cluster, or repeated syncs)."""
    return _dedupe_aurora_serverless_review_findings(findings)


def _aurora_cluster_looks_serverless(configuration: dict) -> bool:
    """
    True when describe_db_clusters data indicates Aurora Serverless v1/v2.

    v2 often uses EngineMode \"provisioned\" with ServerlessV2ScalingConfiguration set;
    the old detector only matched RDS *instances* with db_instance_class db.serverless, so
    typical Serverless v2 clusters (resource_type aurora_cluster) never produced findings.
    """
    mode = (configuration.get("engine_mode") or "").strip().lower()
    if mode == "serverless":
        return True
    sv2 = configuration.get("serverless_v2_scaling")
    if isinstance(sv2, dict) and len(sv2) > 0:
        return True
    scaling = configuration.get("scaling_configuration")
    if isinstance(scaling, dict) and len(scaling) > 0:
        return True
    return False


def detect_rds_findings(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    sync_run_id: UUID,
) -> DetectionRunResult:
    """Analyze latest RDS/Aurora snapshots and append findings rows."""
    cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)

    max_rds_captured = (
        db_session.query(func.max(ResourceSnapshot.captured_at))
        .filter(
            ResourceSnapshot.tenant_id == tenant_id,
            ResourceSnapshot.cloud_account_id == cloud_account_id,
            ResourceSnapshot.resource_type == "rds_instance",
        )
        .scalar()
    )
    max_aurora_captured = (
        db_session.query(func.max(ResourceSnapshot.captured_at))
        .filter(
            ResourceSnapshot.tenant_id == tenant_id,
            ResourceSnapshot.cloud_account_id == cloud_account_id,
            ResourceSnapshot.resource_type == "aurora_cluster",
        )
        .scalar()
    )

    detected_at = utc_now()
    if max_rds_captured is None and max_aurora_captured is None:
        return DetectionRunResult(
            cloud_account_id=cloud_account_id,
            resource_type="rds",
            findings_created=0,
            detected_at=detected_at,
            sync_run_id=sync_run_id,
        )

    snapshot_conditions = []
    if max_rds_captured is not None:
        snapshot_conditions.append(
            and_(
                ResourceSnapshot.resource_type == "rds_instance",
                ResourceSnapshot.captured_at == max_rds_captured,
            )
        )
    if max_aurora_captured is not None:
        snapshot_conditions.append(
            and_(
                ResourceSnapshot.resource_type == "aurora_cluster",
                ResourceSnapshot.captured_at == max_aurora_captured,
            )
        )

    snapshots = (
        db_session.query(ResourceSnapshot)
        .filter(
            ResourceSnapshot.tenant_id == tenant_id,
            ResourceSnapshot.cloud_account_id == cloud_account_id,
            or_(*snapshot_conditions),
        )
        .order_by(ResourceSnapshot.resource_type.asc(), ResourceSnapshot.resource_id.asc())
        .all()
    )

    cluster_cfg_by_id = {
        s.resource_id: s.configuration_json or {}
        for s in snapshots
        if s.resource_type == "aurora_cluster"
    }

    findings: list[Finding] = []
    for snapshot in snapshots:
        tags = snapshot.tags_json or {}
        missing_tags = _missing_required_tags(tags)
        if missing_tags:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="rds_missing_required_tags",
                    severity="medium",
                    evidence_json={"missing_tags": missing_tags, "tags": tags},
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

        configuration = snapshot.configuration_json or {}
        if configuration.get("publicly_accessible") is True:
            if snapshot.resource_type == "rds_instance":
                cid = configuration.get("db_cluster_identifier")
                cluster_cfg = cluster_cfg_by_id.get(cid) if cid else None
                if (
                    cid
                    and isinstance(cluster_cfg, dict)
                    and "publicly_accessible" in cluster_cfg
                ):
                    pass
                else:
                    findings.append(
                        Finding(
                            tenant_id=tenant_id,
                            cloud_account_id=cloud_account_id,
                            resource_snapshot_id=snapshot.id,
                            resource_id=snapshot.resource_id,
                            resource_type=snapshot.resource_type,
                            finding_type="rds_publicly_accessible",
                            severity="high",
                            evidence_json={"publicly_accessible": True},
                            detected_at=detected_at,
                            sync_run_id=sync_run_id,
                        )
                    )
            elif snapshot.resource_type == "aurora_cluster":
                findings.append(
                    Finding(
                        tenant_id=tenant_id,
                        cloud_account_id=cloud_account_id,
                        resource_snapshot_id=snapshot.id,
                        resource_id=snapshot.resource_id,
                        resource_type=snapshot.resource_type,
                        finding_type="rds_publicly_accessible",
                        severity="high",
                        evidence_json={"publicly_accessible": True},
                        detected_at=detected_at,
                        sync_run_id=sync_run_id,
                    )
                )

        if snapshot.resource_type == "rds_instance":
            db_instance_class = configuration.get("db_instance_class")
            raw_env = tags.get("Environment")
            environment = raw_env.strip() if isinstance(raw_env, str) and raw_env.strip() else "unknown"

            if db_instance_class in OVERPROVISIONED_RDS_INSTANCE_TYPES:
                evidence = {"db_instance_class": db_instance_class, "environment": environment}
                findings.append(
                    Finding(
                        tenant_id=tenant_id,
                        cloud_account_id=cloud_account_id,
                        resource_snapshot_id=snapshot.id,
                        resource_id=snapshot.resource_id,
                        resource_type=snapshot.resource_type,
                        finding_type="rds_instance_overprovisioned",
                        severity="medium",
                        evidence_json=evidence,
                        estimated_savings=estimate_monthly_savings_for_finding(
                            finding_type="rds_instance_overprovisioned",
                            evidence_json=evidence,
                            resource_type=snapshot.resource_type,
                        ),
                        detected_at=detected_at,
                        sync_run_id=sync_run_id,
                    )
                )

            if db_instance_class == "db.serverless":
                evidence = {
                    "db_instance_class": db_instance_class,
                    "engine": configuration.get("engine"),
                    "publicly_accessible": configuration.get("publicly_accessible"),
                }
                findings.append(
                    Finding(
                        tenant_id=tenant_id,
                        cloud_account_id=cloud_account_id,
                        resource_snapshot_id=snapshot.id,
                        resource_id=snapshot.resource_id,
                        resource_type=snapshot.resource_type,
                        finding_type="aurora_serverless_review_candidate",
                        severity="medium",
                        evidence_json=evidence,
                        estimated_savings=estimate_monthly_savings_for_finding(
                            finding_type="aurora_serverless_review_candidate",
                            evidence_json=evidence,
                            resource_type=snapshot.resource_type,
                        ),
                        detected_at=detected_at,
                        sync_run_id=sync_run_id,
                    )
                )

        if snapshot.resource_type == "aurora_cluster" and _aurora_cluster_looks_serverless(configuration):
            evidence = {
                "engine": configuration.get("engine"),
                "engine_mode": configuration.get("engine_mode"),
                "serverless_v2_scaling": configuration.get("serverless_v2_scaling"),
                "scaling_configuration": configuration.get("scaling_configuration"),
                "publicly_accessible": configuration.get("publicly_accessible"),
            }
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="aurora_serverless_review_candidate",
                    severity="medium",
                    evidence_json=evidence,
                    estimated_savings=estimate_monthly_savings_for_finding(
                        finding_type="aurora_serverless_review_candidate",
                        evidence_json=evidence,
                        resource_type=snapshot.resource_type,
                    ),
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

    findings = _dedupe_aurora_serverless_review_findings(findings)

    if findings:
        db_session.add_all(findings)
        db_session.commit()

    return DetectionRunResult(
        cloud_account_id=cloud_account_id,
        resource_type="rds",
        findings_created=len(findings),
        detected_at=detected_at,
        sync_run_id=sync_run_id,
    )


def detect_lambda_findings(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    sync_run_id: UUID,
) -> DetectionRunResult:
    """Analyze latest Lambda snapshots and append findings rows."""
    cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)

    latest_captured_at = (
        db_session.query(func.max(ResourceSnapshot.captured_at))
        .filter(
            ResourceSnapshot.tenant_id == tenant_id,
            ResourceSnapshot.cloud_account_id == cloud_account_id,
            ResourceSnapshot.resource_type == "lambda_function",
        )
        .scalar()
    )

    detected_at = utc_now()
    if latest_captured_at is None:
        return DetectionRunResult(
            cloud_account_id=cloud_account_id,
            resource_type="lambda_function",
            findings_created=0,
            detected_at=detected_at,
            sync_run_id=sync_run_id,
        )

    snapshots = (
        db_session.query(ResourceSnapshot)
        .filter(
            ResourceSnapshot.tenant_id == tenant_id,
            ResourceSnapshot.cloud_account_id == cloud_account_id,
            ResourceSnapshot.captured_at == latest_captured_at,
            ResourceSnapshot.resource_type == "lambda_function",
        )
        .order_by(ResourceSnapshot.resource_id.asc())
        .all()
    )

    findings: list[Finding] = []
    for snapshot in snapshots:
        tags = snapshot.tags_json or {}
        missing_tags = _missing_required_tags(tags)
        if missing_tags:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="lambda_missing_required_tags",
                    severity="medium",
                    evidence_json={"missing_tags": missing_tags, "tags": tags},
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

        configuration = snapshot.configuration_json or {}
        memory_size = configuration.get("memory_size")
        if isinstance(memory_size, (int, float)) and memory_size >= 1024:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="lambda_high_memory_configuration_candidate",
                    severity="medium",
                    evidence_json={"memory_size": memory_size},
                    estimated_savings=10,
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

    if findings:
        db_session.add_all(findings)
        db_session.commit()

    return DetectionRunResult(
        cloud_account_id=cloud_account_id,
        resource_type="lambda_function",
        findings_created=len(findings),
        detected_at=detected_at,
        sync_run_id=sync_run_id,
    )


def detect_s3_findings(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    sync_run_id: UUID,
) -> DetectionRunResult:
    """Analyze latest S3 snapshots and append findings rows."""
    cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)

    latest_captured_at = (
        db_session.query(func.max(ResourceSnapshot.captured_at))
        .filter(
            ResourceSnapshot.tenant_id == tenant_id,
            ResourceSnapshot.cloud_account_id == cloud_account_id,
            ResourceSnapshot.resource_type == "s3_bucket",
        )
        .scalar()
    )

    detected_at = utc_now()
    if latest_captured_at is None:
        return DetectionRunResult(
            cloud_account_id=cloud_account_id,
            resource_type="s3_bucket",
            findings_created=0,
            detected_at=detected_at,
            sync_run_id=sync_run_id,
        )

    snapshots = (
        db_session.query(ResourceSnapshot)
        .filter(
            ResourceSnapshot.tenant_id == tenant_id,
            ResourceSnapshot.cloud_account_id == cloud_account_id,
            ResourceSnapshot.captured_at == latest_captured_at,
            ResourceSnapshot.resource_type == "s3_bucket",
        )
        .order_by(ResourceSnapshot.resource_id.asc())
        .all()
    )

    findings: list[Finding] = []
    for snapshot in snapshots:
        tags = snapshot.tags_json or {}
        missing_tags = _missing_required_tags(tags)
        if missing_tags:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="s3_missing_required_tags",
                    severity="medium",
                    evidence_json={"missing_tags": missing_tags, "tags": tags},
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

        configuration = snapshot.configuration_json or {}
        pab_status = configuration.get("public_access_block_status") or {}
        if not pab_status.get("block_public_policy") or not pab_status.get("block_public_acls"):
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="s3_public_access_candidate",
                    severity="high",
                    evidence_json={"public_access_block_status": pab_status},
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

        versioning_status = configuration.get("versioning_status", "unknown")
        if versioning_status != "Enabled":
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="s3_versioning_disabled_candidate",
                    severity="medium",
                    evidence_json={"versioning_status": versioning_status},
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

        lifecycle_rules_count = configuration.get("lifecycle_rules_count", 0)
        if lifecycle_rules_count == 0:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="s3_lifecycle_policy_missing",
                    severity="medium",
                    evidence_json={"lifecycle_rules_count": lifecycle_rules_count},
                    estimated_savings=5,
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

    if findings:
        db_session.add_all(findings)
        db_session.commit()

    return DetectionRunResult(
        cloud_account_id=cloud_account_id,
        resource_type="s3_bucket",
        findings_created=len(findings),
        detected_at=detected_at,
        sync_run_id=sync_run_id,
    )


def list_findings(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> list[Finding]:
    """List findings for a tenant-scoped cloud account."""
    cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)

    rows = (
        db_session.query(Finding)
        .filter(
            Finding.tenant_id == tenant_id,
            Finding.cloud_account_id == cloud_account_id,
        )
        .order_by(Finding.detected_at.desc())
        .all()
    )
    deduped = _dedupe_aurora_serverless_review_findings(list(rows))
    deduped.sort(
        key=lambda f: (f.detected_at.timestamp() if f.detected_at else 0.0, str(f.id)),
        reverse=True,
    )
    return deduped


def _resource_snapshot_id_for_cost_findings(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    ec2_snapshots: list[ResourceSnapshot],
) -> UUID | None:
    """Finding rows require ``resource_snapshot_id``; reuse latest EC2 snapshot or any latest snapshot."""
    if ec2_snapshots:
        return ec2_snapshots[0].id
    fallback = (
        db_session.query(ResourceSnapshot)
        .filter(
            ResourceSnapshot.tenant_id == tenant_id,
            ResourceSnapshot.cloud_account_id == cloud_account_id,
        )
        .order_by(ResourceSnapshot.captured_at.desc(), ResourceSnapshot.id.asc())
        .first()
    )
    return fallback.id if fallback is not None else None


def detect_ec2_findings(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    sync_run_id: UUID,
) -> DetectionRunResult:
    """Analyze latest EC2 snapshots and append findings rows."""
    cloud_account = cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)

    latest_captured_at = (
        db_session.query(func.max(ResourceSnapshot.captured_at))
        .filter(
            ResourceSnapshot.tenant_id == tenant_id,
            ResourceSnapshot.cloud_account_id == cloud_account_id,
            ResourceSnapshot.resource_type == "ec2_instance",
        )
        .scalar()
    )

    detected_at = utc_now()
    snapshots: list[ResourceSnapshot] = []
    if latest_captured_at is not None:
        snapshots = (
            db_session.query(ResourceSnapshot)
            .filter(
                ResourceSnapshot.tenant_id == tenant_id,
                ResourceSnapshot.cloud_account_id == cloud_account_id,
                ResourceSnapshot.captured_at == latest_captured_at,
                ResourceSnapshot.resource_type == "ec2_instance",
            )
            .order_by(ResourceSnapshot.resource_id.asc())
            .all()
        )

    findings: list[Finding] = []
    for snapshot in snapshots:
        tags = snapshot.tags_json or {}
        missing_tags = _missing_required_tags(tags)
        if missing_tags:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="ec2_missing_required_tags",
                    severity="medium",
                    evidence_json={"missing_tags": missing_tags, "tags": tags},
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

        configuration = snapshot.configuration_json or {}
        if configuration.get("state") == "stopped":
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="ec2_stopped_instance",
                    severity="medium",
                    evidence_json={"state": "stopped"},
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

    if findings:
        db_session.add_all(findings)
        db_session.commit()

    cost_snapshot_id = _resource_snapshot_id_for_cost_findings(
        db_session,
        tenant_id,
        cloud_account_id,
        snapshots,
    )
    if cost_snapshot_id is None:
        logger.warning(
            "Skipping cost-based findings (NAT/WAF) due to missing resource snapshot",
            extra={"tenant_id": str(tenant_id), "cloud_account_id": str(cloud_account_id)},
        )
    else:
        try:
            # Reuse the existing EC2-Other cost breakdown logic and extract NAT Gateway spend.
            ec2_other_breakdown = cost_explorer_service.fetch_ec2_other_breakdown(role_arn=cloud_account.role_arn)
            breakdown = ec2_other_breakdown.get("breakdown") or []
            nat_gateway_cost = next(
                (
                    float(item.get("amount", 0.0))
                    for item in breakdown
                    if item.get("category") == "NAT Gateway"
                ),
                0.0,
            )

            if nat_gateway_cost > 0:
                existing_nat_finding = (
                    db_session.query(Finding)
                    .filter(
                        Finding.tenant_id == tenant_id,
                        Finding.cloud_account_id == cloud_account_id,
                        Finding.finding_type == "nat_gateway_cost_review_candidate",
                        Finding.sync_run_id == sync_run_id,
                    )
                    .first()
                )
                if existing_nat_finding is None:
                    nat_finding = Finding(
                        tenant_id=tenant_id,
                        cloud_account_id=cloud_account_id,
                        resource_snapshot_id=cost_snapshot_id,
                        resource_id="nat-gateway-cost",
                        resource_type="nat_gateway",
                        finding_type="nat_gateway_cost_review_candidate",
                        severity="medium",
                        evidence_json={
                            "category": "NAT Gateway",
                            "current_monthly_cost": nat_gateway_cost,
                        },
                        # Deterministic placeholder until we add a real savings model.
                        estimated_savings=10.0,
                        detected_at=detected_at,
                        sync_run_id=sync_run_id,
                    )
                    db_session.add(nat_finding)
                    db_session.commit()
                    findings.append(nat_finding)
        except Exception:
            logger.exception(
                "NAT Gateway cost enrichment failed during EC2 detection",
                extra={"tenant_id": str(tenant_id), "cloud_account_id": str(cloud_account_id)},
            )

        try:
            waf_cost = cost_explorer_service.fetch_aws_waf_monthly_cost(role_arn=cloud_account.role_arn)
            if waf_cost > 0:
                existing_waf_finding = (
                    db_session.query(Finding)
                    .filter(
                        Finding.tenant_id == tenant_id,
                        Finding.cloud_account_id == cloud_account_id,
                        Finding.finding_type == "waf_cost_review_candidate",
                        Finding.sync_run_id == sync_run_id,
                    )
                    .first()
                )
                if existing_waf_finding is None:
                    waf_finding = Finding(
                        tenant_id=tenant_id,
                        cloud_account_id=cloud_account_id,
                        resource_snapshot_id=cost_snapshot_id,
                        resource_id="waf-cost",
                        resource_type="waf",
                        finding_type="waf_cost_review_candidate",
                        severity="medium",
                        evidence_json={
                            "category": "AWS WAF",
                            "current_monthly_cost": waf_cost,
                        },
                        estimated_savings=5.0,
                        detected_at=detected_at,
                        sync_run_id=sync_run_id,
                    )
                    db_session.add(waf_finding)
                    db_session.commit()
                    findings.append(waf_finding)
        except Exception:
            logger.exception(
                "AWS WAF cost enrichment failed during EC2 detection",
                extra={"tenant_id": str(tenant_id), "cloud_account_id": str(cloud_account_id)},
            )

    return DetectionRunResult(
        cloud_account_id=cloud_account_id,
        resource_type="ec2_instance",
        findings_created=len(findings),
        detected_at=detected_at,
        sync_run_id=sync_run_id,
    )


def detect_ebs_findings(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    sync_run_id: UUID,
) -> DetectionRunResult:
    """Analyze latest EBS snapshots and append findings rows."""
    cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)

    latest_captured_at = (
        db_session.query(func.max(ResourceSnapshot.captured_at))
        .filter(
            ResourceSnapshot.tenant_id == tenant_id,
            ResourceSnapshot.cloud_account_id == cloud_account_id,
            ResourceSnapshot.resource_type == "ebs_volume",
        )
        .scalar()
    )

    detected_at = utc_now()
    if latest_captured_at is None:
        return DetectionRunResult(
            cloud_account_id=cloud_account_id,
            resource_type="ebs_volume",
            findings_created=0,
            detected_at=detected_at,
            sync_run_id=sync_run_id,
        )

    snapshots = (
        db_session.query(ResourceSnapshot)
        .filter(
            ResourceSnapshot.tenant_id == tenant_id,
            ResourceSnapshot.cloud_account_id == cloud_account_id,
            ResourceSnapshot.captured_at == latest_captured_at,
            ResourceSnapshot.resource_type == "ebs_volume",
        )
        .order_by(ResourceSnapshot.resource_id.asc())
        .all()
    )

    findings: list[Finding] = []
    for snapshot in snapshots:
        tags = snapshot.tags_json or {}
        missing_tags = _missing_required_tags(tags)
        if missing_tags:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="ebs_missing_required_tags",
                    severity="medium",
                    evidence_json={"missing_tags": missing_tags, "tags": tags},
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

        configuration = snapshot.configuration_json or {}
        if configuration.get("state") == "available":
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="ebs_unattached_volume",
                    severity="medium",
                    evidence_json={"state": "available", "attachments": configuration.get("attachments", [])},
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

    if findings:
        db_session.add_all(findings)
        db_session.commit()

    return DetectionRunResult(
        cloud_account_id=cloud_account_id,
        resource_type="ebs_volume",
        findings_created=len(findings),
        detected_at=detected_at,
        sync_run_id=sync_run_id,
    )
