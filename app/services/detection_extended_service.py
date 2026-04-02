"""Extended governance/security findings for CloudFront, ACM, API Gateway, EventBridge, SES, Lambda, and VPC networking."""

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

    for snapshot in _latest_snapshots(db_session, tenant_id, cloud_account_id, "security_group"):
        cfg = snapshot.configuration_json or {}
        has_open_sensitive = bool(
            cfg.get("has_world_open_ssh") or cfg.get("has_world_open_rdp") or cfg.get("has_world_open_all_ports")
        )
        if has_open_sensitive:
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
