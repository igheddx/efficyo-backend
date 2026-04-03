"""Extended governance/security findings for CloudFront, ACM, API Gateway, EventBridge, SES, IoT, Lambda, and VPC networking."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.db import utc_now
from app.models.finding import Finding
from app.models.resource_snapshot import ResourceSnapshot
from app.services import cloud_account_service
from app.services.finding_templates import build_finding_evidence
from app.services.resource_capability_registry import EXTENDED_TAGGABLE_RESOURCE_TYPES
from app.services.detection_service import DetectionRunResult, _missing_required_tags


def _linked_resource_ref(
    *,
    resource_type: str,
    resource_id: str,
    resource_name: str | None,
    relation: str,
    confidence: str,
    source: str,
) -> dict[str, str]:
    return {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "resource_name": resource_name or "",
        "relation": relation,
        "confidence": confidence,
        "source": source,
    }


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


_OUTDATED_LAMBDA_RUNTIME_PREFIXES = (
    "python2.",
    "python3.6",
    "python3.7",
    "python3.8",
    "nodejs10",
    "nodejs12",
    "nodejs14",
    "dotnetcore2.",
    "dotnetcore3.",
    "ruby2.",
    "java8",
)


def _is_outdated_lambda_runtime(runtime: str | None) -> bool:
    rt = str(runtime or "").strip().lower()
    return bool(rt) and any(rt.startswith(prefix) for prefix in _OUTDATED_LAMBDA_RUNTIME_PREFIXES)


def detect_extended_findings(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    sync_run_id: UUID,
) -> DetectionRunResult:
    """Emit extended governance/security findings from latest snapshots."""
    cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)
    detected_at = utc_now()
    findings: list[Finding] = []

    for rt in EXTENDED_TAGGABLE_RESOURCE_TYPES:
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

    cloudfront_snapshots = _latest_snapshots(db_session, tenant_id, cloud_account_id, "cloudfront_distribution")
    acm_snapshots = _latest_snapshots(db_session, tenant_id, cloud_account_id, "acm_certificate")
    lambda_snapshots = _latest_snapshots(db_session, tenant_id, cloud_account_id, "lambda_function")
    lambda_by_arn_or_name: dict[str, ResourceSnapshot] = {}
    for l in lambda_snapshots:
        cfg_l = l.configuration_json or {}
        arn = str(cfg_l.get("function_arn") or "").strip()
        if arn:
            lambda_by_arn_or_name[arn] = l
        lambda_by_arn_or_name[str(l.resource_id)] = l

    acm_links_by_arn: dict[str, list[dict]] = {}
    for cf in cloudfront_snapshots:
        cfcfg = cf.configuration_json or {}
        for lr in cfcfg.get("linked_resources") or []:
            if str(lr.get("resource_type") or "") != "acm_certificate":
                continue
            acm_arn = str(lr.get("resource_id") or "").strip()
            if not acm_arn:
                continue
            acm_links_by_arn.setdefault(acm_arn, []).append(
                _linked_resource_ref(
                    resource_type="cloudfront_distribution",
                    resource_id=str(cf.resource_id),
                    resource_name=str(cfcfg.get("domain_name") or ""),
                    relation="used_by_distribution",
                    confidence=str(lr.get("confidence") or "unknown"),
                    source="cloudfront_snapshot_link",
                )
            )

    for snapshot in acm_snapshots:
        cfg = snapshot.configuration_json or {}
        na = cfg.get("not_after")
        linked_from_cf = acm_links_by_arn.get(str(snapshot.resource_id), [])
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
            evidence = {"not_after": na, "days_remaining": days}
            if linked_from_cf:
                evidence["linked_resources"] = linked_from_cf
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="acm_certificate_expiring_soon",
                    severity="high",
                    evidence_json=evidence,
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

    for snapshot in cloudfront_snapshots:
        cfg = snapshot.configuration_json or {}
        linked = list(cfg.get("linked_resources") or [])
        viewer_policy = str(cfg.get("viewer_protocol_policy") or "")
        if viewer_policy == "allow-all":
            evidence = {
                "viewer_protocol_policy": viewer_policy,
                "linked_resources": linked,
                "link_confidence": str(cfg.get("link_confidence") or "unknown"),
            }
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="cloudfront_insecure_viewer_protocol_policy",
                    severity="high",
                    evidence_json=evidence,
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )
        if viewer_policy and viewer_policy != "redirect-to-https":
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="cloudfront_missing_https_redirect",
                    severity="medium",
                    evidence_json={
                        "viewer_protocol_policy": viewer_policy,
                        "linked_resources": linked,
                    },
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )
        if cfg.get("enabled") is False:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="cloudfront_disabled_distribution_review",
                    severity="low",
                    evidence_json={
                        "enabled": False,
                        "status": cfg.get("status"),
                        "linked_resources": linked,
                    },
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

    world_open_sg_ids: list[str] = []
    for snapshot in _latest_snapshots(db_session, tenant_id, cloud_account_id, "security_group"):
        cfg = snapshot.configuration_json or {}
        has_open_sensitive = bool(
            cfg.get("has_world_open_ssh") or cfg.get("has_world_open_rdp") or cfg.get("has_world_open_all_ports")
        )
        if has_open_sensitive:
            world_open_sg_ids.append(str(snapshot.resource_id))
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="security_group_world_open_sensitive_port",
                    severity="high",
                    evidence_json={
                        "has_world_open_ssh": bool(cfg.get("has_world_open_ssh")),
                        "has_world_open_rdp": bool(cfg.get("has_world_open_rdp")),
                        "has_world_open_all_ports": bool(cfg.get("has_world_open_all_ports")),
                        "ingress_rule_count": int(cfg.get("ingress_rule_count") or 0),
                    },
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )
        elif int(cfg.get("ingress_rule_count") or 0) >= 8:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="security_group_overly_permissive",
                    severity="medium",
                    evidence_json={"ingress_rule_count": int(cfg.get("ingress_rule_count") or 0)},
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

    for snapshot in _latest_snapshots(db_session, tenant_id, cloud_account_id, "load_balancer"):
        cfg = snapshot.configuration_json or {}
        deletion_protection_enabled = bool(cfg.get("deletion_protection_enabled"))
        target_group_count = int(cfg.get("target_group_count") or 0)
        healthy_target_count = int(cfg.get("healthy_target_count") or 0)
        unhealthy_target_count = int(cfg.get("unhealthy_target_count") or 0)
        listener_count = int(cfg.get("listener_count") or 0)
        listener_forward_action_count = int(cfg.get("listener_forward_action_count") or 0)
        lb_name = str(cfg.get("load_balancer_name") or snapshot.resource_id)

        if not deletion_protection_enabled:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="load_balancer_deletion_protection_disabled",
                    severity="medium",
                    evidence_json=build_finding_evidence(
                        title="Load balancer deletion protection is disabled",
                        summary=(
                            f"Load balancer {lb_name} can be deleted accidentally, increasing "
                            "availability and recovery risk."
                        ),
                        category="security",
                        risk="medium",
                        confidence="high",
                        recommendation_seed="load_balancer_enable_deletion_protection",
                        approval_required=True,
                        execution_eligible=False,
                        evidence={
                            "load_balancer_name": lb_name,
                            "scheme": cfg.get("scheme"),
                            "type": cfg.get("type"),
                            "deletion_protection_enabled": deletion_protection_enabled,
                        },
                    ),
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

        if target_group_count > 0 and healthy_target_count == 0:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="load_balancer_no_healthy_targets",
                    severity="high",
                    evidence_json=build_finding_evidence(
                        title="Load balancer has no healthy targets",
                        summary=(
                            f"Load balancer {lb_name} has target groups but no healthy backends, "
                            "which can cause request failures."
                        ),
                        category="security",
                        risk="high",
                        confidence="high",
                        recommendation_seed="load_balancer_review_target_health",
                        approval_required=True,
                        execution_eligible=False,
                        evidence={
                            "load_balancer_name": lb_name,
                            "target_group_count": target_group_count,
                            "healthy_target_count": healthy_target_count,
                            "linked_resources": list(cfg.get("linked_resources") or []),
                        },
                    ),
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

        if listener_count > 0 and target_group_count > 0 and listener_forward_action_count > 0 and healthy_target_count == 0:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="load_balancer_no_healthy_traffic_path",
                    severity="high",
                    evidence_json=build_finding_evidence(
                        title="Load balancer has no healthy traffic path",
                        summary=(
                            f"Load balancer {lb_name} has listeners and target groups, but none of the "
                            "forwarding paths currently lead to healthy targets."
                        ),
                        category="reliability",
                        risk="high",
                        confidence="high",
                        recommendation_seed="load_balancer_review_target_health",
                        approval_required=True,
                        execution_eligible=False,
                        evidence={
                            "load_balancer_name": lb_name,
                            "listener_count": listener_count,
                            "target_group_count": target_group_count,
                            "healthy_target_count": healthy_target_count,
                            "listener_forward_action_count": listener_forward_action_count,
                            "linked_resources": list(cfg.get("linked_resources") or []),
                        },
                    ),
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

        if listener_count == 0 or target_group_count == 0:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="load_balancer_unused",
                    severity="low",
                    evidence_json=build_finding_evidence(
                        title="Load balancer appears unused",
                        summary=(
                            f"Load balancer {lb_name} has incomplete traffic configuration (missing listeners "
                            "or target groups), suggesting it may be unused or partially configured."
                        ),
                        category="governance",
                        risk="low",
                        confidence="medium",
                        recommendation_seed="load_balancer_review_target_health",
                        approval_required=True,
                        execution_eligible=False,
                        evidence={
                            "load_balancer_name": lb_name,
                            "listener_count": listener_count,
                            "target_group_count": target_group_count,
                        },
                    ),
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

        if healthy_target_count > 0 and unhealthy_target_count > 0:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="load_balancer_partial_failure",
                    severity="medium",
                    evidence_json=build_finding_evidence(
                        title="Load balancer has partial backend failure",
                        summary=(
                            f"Load balancer {lb_name} has both healthy and unhealthy targets, indicating "
                            "degraded backend reliability."
                        ),
                        category="reliability",
                        risk="medium",
                        confidence="high",
                        recommendation_seed="load_balancer_review_target_health",
                        approval_required=True,
                        execution_eligible=False,
                        evidence={
                            "load_balancer_name": lb_name,
                            "healthy_target_count": healthy_target_count,
                            "unhealthy_target_count": unhealthy_target_count,
                            "linked_resources": list(cfg.get("linked_resources") or []),
                        },
                    ),
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

    public_subnet_ids: set[str] = set()
    route_table_with_public_default: list[tuple[ResourceSnapshot, dict]] = []
    subnet_by_id = {
        str(s.resource_id): (s.configuration_json or {})
        for s in _latest_snapshots(db_session, tenant_id, cloud_account_id, "subnet")
    }
    for snapshot in _latest_snapshots(db_session, tenant_id, cloud_account_id, "route_table"):
        cfg = snapshot.configuration_json or {}
        association_count = int(cfg.get("association_count") or 0)
        has_igw_default_route = bool(cfg.get("has_igw_default_route"))
        has_nat_default_route = bool(cfg.get("has_nat_default_route"))
        linked_resources = list(cfg.get("linked_resources") or [])

        if has_igw_default_route:
            route_table_with_public_default.append((snapshot, cfg))
            for linked in linked_resources:
                if str(linked.get("resource_type") or "") != "subnet":
                    continue
                sid = str(linked.get("resource_id") or "").strip()
                if sid:
                    public_subnet_ids.add(sid)

        if association_count == 0:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="route_table_unassociated_review",
                    severity="low",
                    evidence_json=build_finding_evidence(
                        title="Route table has no subnet associations",
                        summary=(
                            "This route table is currently not associated with any subnets and may be "
                            "stale infrastructure that should be reviewed for cleanup."
                        ),
                        category="governance",
                        risk="low",
                        confidence="high",
                        recommendation_seed="route_table_cleanup_unused",
                        approval_required=True,
                        execution_eligible=False,
                        evidence={
                            "association_count": association_count,
                            "route_count": int(cfg.get("route_count") or 0),
                        },
                    ),
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

        if has_igw_default_route and not has_nat_default_route:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="route_table_public_default_route_review",
                    severity="medium",
                    evidence_json=build_finding_evidence(
                        title="Route table has direct public default route",
                        summary=(
                            "This route table sends default traffic through an internet gateway without "
                            "NAT mediation. Confirm this is intended for the attached subnets."
                        ),
                        category="security",
                        risk="medium",
                        confidence="medium",
                        recommendation_seed="route_table_review_public_egress",
                        approval_required=True,
                        execution_eligible=False,
                        evidence={
                            "has_igw_default_route": has_igw_default_route,
                            "has_nat_default_route": has_nat_default_route,
                            "linked_resources": linked_resources,
                        },
                    ),
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

    for snapshot in _latest_snapshots(db_session, tenant_id, cloud_account_id, "target_group"):
        cfg = snapshot.configuration_json or {}
        tg_name = str(cfg.get("target_group_name") or snapshot.resource_id)
        healthy_count = int(cfg.get("healthy_count") or 0)
        unhealthy_count = int(cfg.get("unhealthy_count") or 0)
        total_targets = int(cfg.get("total_targets") or 0)
        stickiness_enabled = bool(cfg.get("stickiness_enabled"))
        deregistration_delay_seconds = int(cfg.get("deregistration_delay_seconds") or 30)
        health_check_enabled = bool(cfg.get("health_check_enabled"))
        health_check_interval_seconds = int(cfg.get("health_check_interval_seconds") or 0)
        health_check_timeout_seconds = int(cfg.get("health_check_timeout_seconds") or 0)
        unhealthy_threshold_count = int(cfg.get("unhealthy_threshold_count") or 0)

        if total_targets == 0:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="target_group_no_targets",
                    severity="medium",
                    evidence_json=build_finding_evidence(
                        title="Target group has no registered targets",
                        summary=(
                            f"Target group {tg_name} has no registered targets and cannot receive application traffic."
                        ),
                        category="reliability",
                        risk="medium",
                        confidence="high",
                        recommendation_seed="attach_targets_or_cleanup",
                        approval_required=True,
                        execution_eligible=False,
                        evidence={
                            "target_group_name": tg_name,
                            "total_targets": total_targets,
                            "load_balancer_arn": cfg.get("load_balancer_arn"),
                        },
                    ),
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

        if unhealthy_count > 0:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="target_group_unhealthy_targets",
                    severity="high",
                    evidence_json=build_finding_evidence(
                        title="Target group has unhealthy targets",
                        summary=(
                            f"Target group {tg_name} has unhealthy backends that can degrade or fail user traffic."
                        ),
                        category="reliability",
                        risk="high",
                        confidence="high",
                        recommendation_seed="investigate_unhealthy_targets",
                        approval_required=True,
                        execution_eligible=False,
                        evidence={
                            "target_group_name": tg_name,
                            "healthy_count": healthy_count,
                            "unhealthy_count": unhealthy_count,
                            "target_health_states": cfg.get("target_health_states") or {},
                        },
                    ),
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

        if (
            not health_check_enabled
            or health_check_interval_seconds <= 0
            or health_check_timeout_seconds <= 0
            or unhealthy_threshold_count <= 0
        ):
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="target_group_misconfigured_health_check",
                    severity="medium",
                    evidence_json=build_finding_evidence(
                        title="Target group health check appears misconfigured",
                        summary=(
                            f"Target group {tg_name} has missing or weak health check settings that may delay "
                            "failure detection."
                        ),
                        category="reliability",
                        risk="medium",
                        confidence="medium",
                        recommendation_seed="fix_health_check_configuration",
                        approval_required=False,
                        execution_eligible=True,
                        evidence={
                            "target_group_name": tg_name,
                            "health_check_enabled": health_check_enabled,
                            "health_check_interval_seconds": health_check_interval_seconds,
                            "health_check_timeout_seconds": health_check_timeout_seconds,
                            "unhealthy_threshold_count": unhealthy_threshold_count,
                            "health_check_path": cfg.get("health_check_path"),
                        },
                    ),
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

        if total_targets > 0 and healthy_count == 0:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="target_group_no_healthy_targets",
                    severity="high",
                    evidence_json=build_finding_evidence(
                        title="Target group has no healthy targets",
                        summary=(
                            f"Target group {tg_name} has registered targets but none are healthy. "
                            "This will cause traffic to fail or be dropped."
                        ),
                        category="security",
                        risk="high",
                        confidence="high",
                        recommendation_seed="target_group_review_target_health",
                        approval_required=True,
                        execution_eligible=False,
                        evidence={
                            "target_group_name": tg_name,
                            "healthy_count": healthy_count,
                            "unhealthy_count": unhealthy_count,
                            "total_targets": total_targets,
                        },
                    ),
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

        if not stickiness_enabled:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="target_group_stickiness_disabled",
                    severity="low",
                    evidence_json=build_finding_evidence(
                        title="Target group has session stickiness disabled",
                        summary=(
                            f"Target group {tg_name} does not have session stickiness enabled. "
                            "Enable stickiness if targets maintain stateful connections or sessions."
                        ),
                        category="reliability",
                        risk="low",
                        confidence="medium",
                        recommendation_seed="target_group_enable_stickiness",
                        approval_required=False,
                        execution_eligible=True,
                        evidence={
                            "target_group_name": tg_name,
                            "stickiness_enabled": stickiness_enabled,
                        },
                    ),
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

        if deregistration_delay_seconds > 60:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="target_group_slow_deregistration",
                    severity="low",
                    evidence_json=build_finding_evidence(
                        title="Target group has long deregistration delay",
                        summary=(
                            f"Target group {tg_name} has a deregistration delay of {deregistration_delay_seconds}s. "
                            "Consider reducing this to minimize traffic loss during deployments."
                        ),
                        category="reliability",
                        risk="low",
                        confidence="high",
                        recommendation_seed="target_group_optimize_deregistration_delay",
                        approval_required=False,
                        execution_eligible=True,
                        evidence={
                            "target_group_name": tg_name,
                            "deregistration_delay_seconds": deregistration_delay_seconds,
                        },
                    ),
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

    if world_open_sg_ids and public_subnet_ids:
        example_public_subnets = sorted(public_subnet_ids)[:10]
        for sg_snapshot in _latest_snapshots(db_session, tenant_id, cloud_account_id, "security_group"):
            if str(sg_snapshot.resource_id) not in set(world_open_sg_ids):
                continue
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=sg_snapshot.id,
                    resource_id=sg_snapshot.resource_id,
                    resource_type=sg_snapshot.resource_type,
                    finding_type="public_subnet_with_open_sg",
                    severity="high",
                    evidence_json=build_finding_evidence(
                        title="Public subnet exposure combined with open security group",
                        summary=(
                            "Detected public subnet routing to an internet gateway while a security group exposes "
                            "sensitive ingress to the internet."
                        ),
                        category="security",
                        risk="high",
                        confidence="medium",
                        recommendation_seed="security_group_restrict_world_open_ports",
                        approval_required=True,
                        execution_eligible=False,
                        evidence={
                            "security_group_id": str(sg_snapshot.resource_id),
                            "public_subnet_ids": example_public_subnets,
                        },
                    ),
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

    if world_open_sg_ids and route_table_with_public_default:
        sg_set = sorted(set(world_open_sg_ids))
        for rt_snapshot, rt_cfg in route_table_with_public_default:
            linked = list(rt_cfg.get("linked_resources") or [])
            associated_subnets = [
                str(x.get("resource_id") or "")
                for x in linked
                if str(x.get("resource_type") or "") == "subnet"
            ]
            has_public_launch_subnet = any(bool((subnet_by_id.get(s) or {}).get("map_public_ip_on_launch")) for s in associated_subnets)
            if not associated_subnets and not has_public_launch_subnet:
                continue
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=rt_snapshot.id,
                    resource_id=rt_snapshot.resource_id,
                    resource_type=rt_snapshot.resource_type,
                    finding_type="internet_exposed_resource_chain",
                    severity="high",
                    evidence_json=build_finding_evidence(
                        title="Internet exposure chain detected",
                        summary=(
                            "Detected an exposure chain across route table, public subnet behavior, and "
                            "world-open security groups."
                        ),
                        category="security",
                        risk="high",
                        confidence="medium",
                        recommendation_seed="route_table_review_public_egress",
                        approval_required=True,
                        execution_eligible=False,
                        evidence={
                            "route_table_id": str(rt_snapshot.resource_id),
                            "associated_subnets": associated_subnets,
                            "world_open_security_groups": sg_set,
                            "has_igw_default_route": bool(rt_cfg.get("has_igw_default_route")),
                        },
                    ),
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

    for snapshot in _latest_snapshots(db_session, tenant_id, cloud_account_id, "apigateway_http_api"):
        cfg = snapshot.configuration_json or {}
        endpoint = str(cfg.get("api_endpoint") or "")
        linked_lambda_arns = [str(x) for x in (cfg.get("linked_lambda_arns") or []) if x]
        linked_lambda_refs: list[dict] = []
        for arn in linked_lambda_arns:
            lm = lambda_by_arn_or_name.get(arn)
            if lm is not None:
                linked_lambda_refs.append(
                    _linked_resource_ref(
                        resource_type="lambda_function",
                        resource_id=str(lm.resource_id),
                        resource_name=str((lm.configuration_json or {}).get("function_arn") or lm.resource_id),
                        relation="fronts_lambda",
                        confidence="direct_integration_uri",
                        source="apigatewayv2.get_integrations",
                    )
                )
            else:
                linked_lambda_refs.append(
                    _linked_resource_ref(
                        resource_type="lambda_function",
                        resource_id=arn,
                        resource_name=arn.rsplit(":", 1)[-1],
                        relation="fronts_lambda",
                        confidence="direct_integration_uri",
                        source="apigatewayv2.get_integrations",
                    )
                )
        if endpoint:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="apigateway_public_exposure_review",
                    severity="medium",
                    evidence_json={
                        "api_endpoint": endpoint,
                        "integration_type": str(cfg.get("integration_type") or "unknown"),
                        "linked_resources": linked_lambda_refs,
                    },
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

    for snapshot in _latest_snapshots(db_session, tenant_id, cloud_account_id, "apigateway_rest_api"):
        cfg = snapshot.configuration_json or {}
        if bool(cfg.get("disable_execute_api_endpoint")):
            continue
        linked_lambda_arns = [str(x) for x in (cfg.get("linked_lambda_arns") or []) if x]
        linked_lambda_refs: list[dict] = []
        for arn in linked_lambda_arns:
            lm = lambda_by_arn_or_name.get(arn)
            if lm is not None:
                linked_lambda_refs.append(
                    _linked_resource_ref(
                        resource_type="lambda_function",
                        resource_id=str(lm.resource_id),
                        resource_name=str((lm.configuration_json or {}).get("function_arn") or lm.resource_id),
                        relation="fronts_lambda",
                        confidence="direct_integration_uri",
                        source="apigateway.get_integration",
                    )
                )
            else:
                linked_lambda_refs.append(
                    _linked_resource_ref(
                        resource_type="lambda_function",
                        resource_id=arn,
                        resource_name=arn.rsplit(":", 1)[-1],
                        relation="fronts_lambda",
                        confidence="direct_integration_uri",
                        source="apigateway.get_integration",
                    )
                )
        findings.append(
            Finding(
                tenant_id=tenant_id,
                cloud_account_id=cloud_account_id,
                resource_snapshot_id=snapshot.id,
                resource_id=snapshot.resource_id,
                resource_type=snapshot.resource_type,
                finding_type="apigateway_public_exposure_review",
                severity="medium",
                evidence_json={
                    "api_type": "rest",
                    "integration_type": str(cfg.get("integration_type") or "unknown"),
                    "linked_resources": linked_lambda_refs,
                },
                detected_at=detected_at,
                sync_run_id=sync_run_id,
            )
        )

    for snapshot in _latest_snapshots(db_session, tenant_id, cloud_account_id, "eventbridge_rule"):
        cfg = snapshot.configuration_json or {}
        target_count = int(cfg.get("target_count") or 0)
        state = str(cfg.get("state") or "").upper()
        if target_count == 0:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="eventbridge_rule_without_targets",
                    severity="medium",
                    evidence_json={"target_count": 0, "state": state or "UNKNOWN"},
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )
        if state == "DISABLED":
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="eventbridge_rule_disabled_review",
                    severity="low",
                    evidence_json={"state": "DISABLED", "target_count": target_count},
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

    for snapshot in _latest_snapshots(db_session, tenant_id, cloud_account_id, "ses_email_identity"):
        cfg = snapshot.configuration_json or {}
        verification = str(cfg.get("verification_status") or "").upper()
        sending_enabled = cfg.get("sending_enabled")
        if verification and verification != "SUCCESS":
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="ses_identity_unverified",
                    severity="medium",
                    evidence_json={
                        "verification_status": verification,
                        "sending_enabled": sending_enabled,
                    },
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )
        if sending_enabled is False:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="ses_sending_disabled_identity",
                    severity="medium",
                    evidence_json={
                        "verification_status": verification or "UNKNOWN",
                        "sending_enabled": False,
                    },
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

    for snapshot in acm_snapshots:
        cfg = snapshot.configuration_json or {}
        status = str(cfg.get("status") or "").upper()
        linked_resources = list(cfg.get("linked_resources") or [])
        if status == "PENDING_VALIDATION":
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="acm_certificate_pending_validation",
                    severity="medium",
                    evidence_json={"status": status, "linked_resources": linked_resources},
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )
        if status in {"FAILED", "VALIDATION_TIMED_OUT", "REVOKED"}:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="acm_certificate_validation_issue",
                    severity="high",
                    evidence_json={"status": status, "linked_resources": linked_resources},
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

    for snapshot in _latest_snapshots(db_session, tenant_id, cloud_account_id, "lambda_function"):
        cfg = snapshot.configuration_json or {}
        runtime = str(cfg.get("runtime") or "").strip()
        timeout = cfg.get("timeout")
        linked_resources = list(cfg.get("linked_resources") or [])
        if _is_outdated_lambda_runtime(runtime):
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="lambda_outdated_runtime",
                    severity="high",
                    evidence_json={"runtime": runtime, "linked_resources": linked_resources},
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )
        if isinstance(timeout, int) and timeout >= 120:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="lambda_review_timeout_configuration",
                    severity="medium",
                    evidence_json={
                        "timeout": timeout,
                        "linked_resources": linked_resources,
                        "vpc_link_status": cfg.get("vpc_link_status") or "unknown",
                    },
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

    for snapshot in _latest_snapshots(db_session, tenant_id, cloud_account_id, "rds_parameter_group"):
        cfg = snapshot.configuration_json or {}
        pg_name = str(cfg.get("parameter_group_name") or snapshot.resource_id)
        slow_query_enabled = bool(cfg.get("slow_query_log_enabled"))
        general_log_enabled = bool(cfg.get("general_log_enabled"))

        if not slow_query_enabled:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="rds_parameter_group_slow_query_disabled",
                    severity="low",
                    evidence_json=build_finding_evidence(
                        title="RDS parameter group has slow query logging disabled",
                        summary=(
                            f"Parameter group {pg_name} does not have slow query logging enabled. "
                            "Enabling it provides visibility into long-running queries for optimization."
                        ),
                        category="reliability",
                        risk="low",
                        confidence="high",
                        recommendation_seed="rds_parameter_group_enable_slow_query_log",
                        approval_required=False,
                        execution_eligible=True,
                        evidence={
                            "parameter_group_name": pg_name,
                            "slow_query_log_enabled": slow_query_enabled,
                        },
                    ),
                    detected_at=detected_at,
                    sync_run_id=sync_run_id,
                )
            )

        if general_log_enabled:
            findings.append(
                Finding(
                    tenant_id=tenant_id,
                    cloud_account_id=cloud_account_id,
                    resource_snapshot_id=snapshot.id,
                    resource_id=snapshot.resource_id,
                    resource_type=snapshot.resource_type,
                    finding_type="rds_parameter_group_general_log_enabled",
                    severity="medium",
                    evidence_json=build_finding_evidence(
                        title="RDS parameter group has general query logging enabled",
                        summary=(
                            f"Parameter group {pg_name} has general query logging enabled. "
                            "This logs all queries and can impact database performance; typically use slow query log instead."
                        ),
                        category="reliability",
                        risk="medium",
                        confidence="high",
                        recommendation_seed="rds_parameter_group_disable_general_log",
                        approval_required=False,
                        execution_eligible=True,
                        evidence={
                            "parameter_group_name": pg_name,
                            "general_log_enabled": general_log_enabled,
                        },
                    ),
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
