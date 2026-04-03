"""
Extended AWS discovery for CloudFront, ACM, API Gateway, EventBridge, SES, IoT,
ELBv2 load balancers, and VPC networking.

All resources normalize to ``resource_snapshot_service.create_snapshots`` rows:
``resource_id``, ``resource_type``, ``region``, ``configuration_json``, ``tags_json``.

Uses the same role assumption and multi-region behavior as ``aws_inventory_service``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from app.services.aws_inventory_service import (
    _create_assumed_client,
    _gather_per_region,
    _normalize_tags,
)

logger = logging.getLogger(__name__)

_SKIPPABLE = frozenset({"UnauthorizedOperation", "AuthFailure", "AccessDenied", "AccessDeniedException"})

# Cap very large accounts (Phase 1); raise in logs when hit.
_MAX_SECURITY_GROUPS_PER_REGION = 2000


def _resource_envelope(
    *,
    resource_id: str,
    resource_type: str,
    region: str,
    configuration_json: dict[str, Any],
    tags_json: dict[str, str],
) -> dict[str, Any]:
    """Normalized snapshot payload used across extended collectors."""
    return {
        "resource_id": resource_id,
        "resource_type": resource_type,
        "region": region,
        "configuration_json": configuration_json,
        "tags_json": tags_json,
    }


def _is_world_open(ip_ranges: list[dict] | None, ipv6_ranges: list[dict] | None) -> bool:
    for r in ip_ranges or []:
        if str((r or {}).get("CidrIp") or "").strip() == "0.0.0.0/0":
            return True
    for r in ipv6_ranges or []:
        if str((r or {}).get("CidrIpv6") or "").strip() == "::/0":
            return True
    return False


def _summarize_world_open_ports(ip_permissions: list[dict] | None) -> tuple[bool, bool, bool]:
    has_ssh = False
    has_rdp = False
    has_all = False
    for perm in ip_permissions or []:
        if not _is_world_open(perm.get("IpRanges"), perm.get("Ipv6Ranges")):
            continue
        proto = str(perm.get("IpProtocol") or "")
        from_port = perm.get("FromPort")
        to_port = perm.get("ToPort")
        if proto == "-1":
            has_all = True
            continue
        if from_port is None or to_port is None:
            continue
        if int(from_port) <= 22 <= int(to_port):
            has_ssh = True
        if int(from_port) <= 3389 <= int(to_port):
            has_rdp = True
        if int(from_port) == 0 and int(to_port) >= 65535:
            has_all = True
    return has_ssh, has_rdp, has_all


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


def _lambda_arn_from_integration_uri(uri: str) -> str | None:
    """
    Extract Lambda function ARN from API Gateway integration URI.

    Expected shape often contains: ``.../functions/<lambda-arn>/invocations``.
    Returns None when URI does not clearly reference Lambda.
    """
    u = str(uri or "")
    if ":lambda:" not in u or ":function:" not in u:
        return None
    if "/functions/" in u:
        u = u.split("/functions/", 1)[1]
    u = u.split("/invocations", 1)[0]
    u = u.strip()
    return u or None


def _safe_list_tags_cloudfront(cf_client, arn: str) -> dict[str, str]:
    try:
        r = cf_client.list_tags_for_resource(Resource=arn)
        items = r.get("Tags", {}).get("Items", []) or []
        return _normalize_tags(items)
    except (ClientError, BotoCoreError) as exc:
        logger.debug("cloudfront list_tags failed: %s", exc)
        return {}


def fetch_cloudfront_distributions(role_arn: str, home_region: str, external_id: str | None = None) -> list[dict]:
    """Global CloudFront distributions (API region us-east-1)."""
    region = "us-east-1"
    out: list[dict] = []
    try:
        cf = _create_assumed_client("cloudfront", role_arn, region, external_id=external_id)
        sts = _create_assumed_client("sts", role_arn, home_region, external_id=external_id)
        account = str(sts.get_caller_identity().get("Account") or "")
        marker: str | None = None
        while True:
            kwargs: dict[str, Any] = {"MaxItems": "100"}
            if marker:
                kwargs["Marker"] = marker
            resp = cf.list_distributions(**kwargs)
            dlist = resp.get("DistributionList") or {}
            items = dlist.get("Items") or []
            for d in items:
                did = d.get("Id")
                if not did:
                    continue
                arn = f"arn:aws:cloudfront::{account}:distribution/{did}"
                tags = _safe_list_tags_cloudfront(cf, arn)
                aliases = d.get("Aliases", {}) or {}
                cfg = {
                    "enabled": d.get("Enabled"),
                    "domain_name": d.get("DomainName"),
                    "status": d.get("Status"),
                    "aliases": aliases.get("Items", []) if isinstance(aliases, dict) else [],
                    "viewer_protocol_policy": (d.get("DefaultCacheBehavior") or {}).get("ViewerProtocolPolicy"),
                    "distribution_arn": arn,
                    "viewer_certificate_iam_certificate_id": (d.get("ViewerCertificate") or {}).get("IAMCertificateId"),
                    "viewer_certificate_acm_arn": (d.get("ViewerCertificate") or {}).get("ACMCertificateArn"),
                    "viewer_certificate_minimum_protocol_version": (d.get("ViewerCertificate") or {}).get(
                        "MinimumProtocolVersion"
                    ),
                    "linked_resources": [],
                }
                out.append(
                    {
                        "resource_id": did,
                        "resource_type": "cloudfront_distribution",
                        "region": "global",
                        "configuration_json": cfg,
                        "tags_json": tags,
                    }
                )
            if not dlist.get("IsTruncated"):
                break
            marker = dlist.get("NextMarker")
            if not marker:
                break
        logger.info("Extended inventory: %d CloudFront distributions", len(out))
    except (ClientError, BotoCoreError) as exc:
        logger.warning("CloudFront inventory failed: %s", exc)
    return out


def _acm_one_region(role_arn: str, region: str, external_id: str | None = None) -> list[dict]:
    acm = _create_assumed_client("acm", role_arn, region, external_id=external_id)
    out: list[dict] = []
    paginator = acm.get_paginator("list_certificates")
    for page in paginator.paginate():
        for summary in page.get("CertificateSummaryList", []) or []:
            arn = summary.get("CertificateArn")
            if not arn:
                continue
            try:
                detail = acm.describe_certificate(CertificateArn=arn).get("Certificate", {}) or {}
            except (ClientError, BotoCoreError):
                detail = dict(summary)
            not_after = detail.get("NotAfter")
            na_iso = not_after.isoformat() if isinstance(not_after, datetime) else None
            tags: dict[str, str] = {}
            try:
                tr = acm.list_tags_for_certificate(CertificateArn=arn)
                tags = _normalize_tags(tr.get("Tags", []))
            except (ClientError, BotoCoreError):
                pass
            cfg = {
                "domain_name": detail.get("DomainName"),
                "subject_alternative_names": detail.get("SubjectAlternativeNames") or [],
                "status": detail.get("Status") or summary.get("Status"),
                "type": detail.get("Type"),
                "in_use": detail.get("InUse"),
                "key_algorithm": detail.get("KeyAlgorithm"),
                "not_after": na_iso,
                "not_before": detail.get("NotBefore").isoformat() if isinstance(detail.get("NotBefore"), datetime) else None,
                "linked_resources": [],
            }
            out.append(
                {
                    "resource_id": arn,
                    "resource_type": "acm_certificate",
                    "region": region,
                    "configuration_json": cfg,
                    "tags_json": tags,
                }
            )
    return out


def fetch_acm_certificates(role_arn: str, home_region: str, external_id: str | None = None) -> list[dict]:
    return _gather_per_region(
        role_arn,
        home_region,
        lambda r: _acm_one_region(role_arn, r, external_id),
        "ACM",
        external_id=external_id,
    )


def _apigw_rest_one_region(role_arn: str, region: str, external_id: str | None = None) -> list[dict]:
    client = _create_assumed_client("apigateway", role_arn, region, external_id=external_id)
    out: list[dict] = []
    try:
        items: list[dict] = []
        pos: str | None = None
        while True:
            kwargs: dict[str, Any] = {"limit": 500}
            if pos:
                kwargs["position"] = pos
            chunk = client.get_rest_apis(**kwargs)
            items.extend(chunk.get("items", []) or [])
            pos = chunk.get("position")
            if not pos:
                break
    except (ClientError, BotoCoreError) as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
        if code in _SKIPPABLE:
            return []
        raise
    for api in items:
        rid = api.get("id")
        if not rid:
            continue
        tags: dict[str, str] = {}
        rest_arn = f"arn:aws:apigateway:{region}::/restapis/{rid}"
        try:
            tags = client.get_tags(resourceArn=rest_arn).get("tags", {}) or {}
        except (ClientError, BotoCoreError):
            pass
        linked_lambda_arns: list[str] = []
        integration_types: set[str] = set()
        try:
            pos_res: str | None = None
            resources: list[dict] = []
            while True:
                kwargs_res: dict[str, Any] = {"restApiId": rid, "limit": 500}
                if pos_res:
                    kwargs_res["position"] = pos_res
                res_chunk = client.get_resources(**kwargs_res)
                resources.extend(res_chunk.get("items", []) or [])
                pos_res = res_chunk.get("position")
                if not pos_res:
                    break

            for res in resources:
                resource_id = str(res.get("id") or "")
                methods = res.get("resourceMethods") or {}
                for method_name in methods.keys():
                    if not resource_id:
                        continue
                    try:
                        integ = client.get_integration(
                            restApiId=rid,
                            resourceId=resource_id,
                            httpMethod=str(method_name),
                        )
                    except (ClientError, BotoCoreError):
                        continue
                    itype = str(integ.get("type") or "")
                    if itype:
                        integration_types.add(itype)
                    arn = _lambda_arn_from_integration_uri(str(integ.get("uri") or ""))
                    if arn and arn not in linked_lambda_arns:
                        linked_lambda_arns.append(arn)
        except (ClientError, BotoCoreError):
            pass

        cfg = {
            "name": api.get("name"),
            "created_date": str(api.get("createdDate", "")),
            "disable_execute_api_endpoint": bool(api.get("disableExecuteApiEndpoint", False)),
            "linked_lambda_arns": linked_lambda_arns,
            "linked_lambda_names": [a.rsplit(":", 1)[-1] for a in linked_lambda_arns],
            "integration_type": ",".join(sorted(integration_types)) if integration_types else "unknown",
            "integration_source": "apigateway.get_resources/get_integration",
            "linked_resources": [
                _linked_resource_ref(
                    resource_type="lambda_function",
                    resource_id=arn,
                    resource_name=arn.rsplit(":", 1)[-1],
                    relation="fronts_lambda",
                    confidence="direct_integration_uri",
                    source="apigateway.get_integration",
                )
                for arn in linked_lambda_arns
            ],
        }
        out.append(
            {
                "resource_id": rid,
                "resource_type": "apigateway_rest_api",
                "region": region,
                "configuration_json": cfg,
                "tags_json": tags if isinstance(tags, dict) else {},
            }
        )
    return out


def fetch_apigateway_rest_apis(role_arn: str, home_region: str, external_id: str | None = None) -> list[dict]:
    return _gather_per_region(
        role_arn,
        home_region,
        lambda r: _apigw_rest_one_region(role_arn, r, external_id),
        "API Gateway REST",
        external_id=external_id,
    )


def _apigw_http_one_region(role_arn: str, region: str, external_id: str | None = None) -> list[dict]:
    client = _create_assumed_client("apigatewayv2", role_arn, region, external_id=external_id)
    out: list[dict] = []
    try:
        resp = client.get_apis()
    except (ClientError, BotoCoreError) as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
        if code in _SKIPPABLE:
            return []
        raise
    for api in resp.get("Items", []) or []:
        aid = api.get("ApiId")
        if not aid:
            continue
        cfg = {
            "name": api.get("Name"),
            "protocol_type": api.get("ProtocolType"),
            "api_endpoint": api.get("ApiEndpoint"),
            "linked_lambda_arns": [],
            "linked_lambda_names": [],
            "integration_type": "unknown",
            "integration_source": "apigatewayv2.get_integrations",
            "linked_resources": [],
        }
        integration_lambda_arns: list[str] = []
        integration_types: set[str] = set()
        try:
            ir = client.get_integrations(ApiId=aid)
            for integ in ir.get("Items", []) or []:
                itype = str(integ.get("IntegrationType") or "")
                if itype:
                    integration_types.add(itype)
                arn = _lambda_arn_from_integration_uri(str(integ.get("IntegrationUri") or ""))
                if arn and arn not in integration_lambda_arns:
                    integration_lambda_arns.append(arn)
        except (ClientError, BotoCoreError):
            pass
        if integration_lambda_arns:
            cfg["integration_type"] = "lambda_proxy_or_lambda"
            cfg["linked_lambda_arns"] = integration_lambda_arns
            cfg["linked_lambda_names"] = [a.rsplit(":", 1)[-1] for a in integration_lambda_arns]
            cfg["linked_resources"] = [
                _linked_resource_ref(
                    resource_type="lambda_function",
                    resource_id=arn,
                    resource_name=arn.rsplit(":", 1)[-1],
                    relation="fronts_lambda",
                    confidence="direct_integration_uri",
                    source="apigatewayv2.get_integrations",
                )
                for arn in integration_lambda_arns
            ]
        elif integration_types:
            cfg["integration_type"] = ",".join(sorted(integration_types))
        out.append(
            {
                "resource_id": aid,
                "resource_type": "apigateway_http_api",
                "region": region,
                "configuration_json": cfg,
                "tags_json": api.get("Tags") or {},
            }
        )
    return out


def fetch_apigateway_http_apis(role_arn: str, home_region: str, external_id: str | None = None) -> list[dict]:
    return _gather_per_region(
        role_arn,
        home_region,
        lambda r: _apigw_http_one_region(role_arn, r, external_id),
        "API Gateway HTTP",
        external_id=external_id,
    )


def _events_rules_one_region(role_arn: str, region: str, external_id: str | None = None) -> list[dict]:
    client = _create_assumed_client("events", role_arn, region, external_id=external_id)
    out: list[dict] = []
    paginator = client.get_paginator("list_rules")
    for page in paginator.paginate():
        for rule in page.get("Rules", []) or []:
            name = rule.get("Name")
            if not name:
                continue
            cfg = {
                "arn": rule.get("Arn"),
                "state": rule.get("State"),
                "schedule_expression": rule.get("ScheduleExpression"),
                "event_bus_name": rule.get("EventBusName", "default"),
                "target_count": 0,
            }
            bus = cfg.get("event_bus_name") or "default"
            try:
                t = client.list_targets_by_rule(Rule=name, EventBusName=bus)
                cfg["target_count"] = len(t.get("Targets", []) or [])
            except (ClientError, BotoCoreError):
                cfg["target_count"] = 0
            rid = f"{bus}/{name}"[:255]
            out.append(
                {
                    "resource_id": rid,
                    "resource_type": "eventbridge_rule",
                    "region": region,
                    "configuration_json": cfg,
                    "tags_json": {},
                }
            )
    return out


def fetch_eventbridge_rules(role_arn: str, home_region: str, external_id: str | None = None) -> list[dict]:
    return _gather_per_region(
        role_arn,
        home_region,
        lambda r: _events_rules_one_region(role_arn, r, external_id),
        "EventBridge",
        external_id=external_id,
    )


def _ses_identities_one_region(role_arn: str, region: str, external_id: str | None = None) -> list[dict]:
    client = _create_assumed_client("sesv2", role_arn, region, external_id=external_id)
    out: list[dict] = []
    paginator = client.get_paginator("list_email_identities")
    for page in paginator.paginate():
        for ident in page.get("EmailIdentities", []) or []:
            if isinstance(ident, str):
                name = ident
                itype = None
            else:
                name = (ident or {}).get("IdentityName")
                itype = (ident or {}).get("IdentityType")
            if not name:
                continue
            cfg: dict[str, Any] = {"identity_type": itype}
            try:
                got = client.get_email_identity(EmailIdentity=name)
                cfg["verification_status"] = got.get("VerificationStatus")
                cfg["sending_enabled"] = got.get("SendingEnabled")
            except (ClientError, BotoCoreError):
                pass
            rid = name[:255]
            out.append(
                {
                    "resource_id": rid,
                    "resource_type": "ses_email_identity",
                    "region": region,
                    "configuration_json": cfg,
                    "tags_json": {},
                }
            )
    return out


def fetch_ses_email_identities(role_arn: str, home_region: str, external_id: str | None = None) -> list[dict]:
    return _gather_per_region(
        role_arn,
        home_region,
        lambda r: _ses_identities_one_region(role_arn, r, external_id),
        "SES",
        external_id=external_id,
    )


def _iot_things_one_region(role_arn: str, region: str, external_id: str | None = None) -> list[dict]:
    """Collect AWS IoT Things for governance visibility."""
    client = _create_assumed_client("iot", role_arn, region, external_id=external_id)
    out: list[dict] = []
    marker: str | None = None
    while True:
        kwargs: dict[str, Any] = {"maxResults": 250}
        if marker:
            kwargs["nextToken"] = marker
        try:
            resp = client.list_things(**kwargs)
        except (ClientError, BotoCoreError) as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if code in _SKIPPABLE:
                return []
            raise

        for thing in resp.get("things", []) or []:
            thing_name = str(thing.get("thingName") or "").strip()
            if not thing_name:
                continue

            thing_arn = str(thing.get("thingArn") or "").strip()
            thing_type_name = str(thing.get("thingTypeName") or "").strip()
            attrs = thing.get("attributes") if isinstance(thing.get("attributes"), dict) else {}

            tags: dict[str, str] = {}
            if thing_arn:
                try:
                    tr = client.list_tags_for_resource(resourceArn=thing_arn)
                    tags = _normalize_tags(tr.get("tags", []))
                except (ClientError, BotoCoreError):
                    # Tag listing often requires separate permissions; keep ingestion resilient.
                    tags = {}

            out.append(
                {
                    "resource_id": thing_name,
                    "resource_type": "iot_thing",
                    "region": region,
                    "configuration_json": {
                        "thing_arn": thing_arn,
                        "thing_type_name": thing_type_name,
                        "attributes": attrs,
                        "version": thing.get("version"),
                    },
                    "tags_json": tags,
                }
            )

        marker = resp.get("nextToken")
        if not marker:
            break

    return out


def fetch_iot_things(role_arn: str, home_region: str, external_id: str | None = None) -> list[dict]:
    return _gather_per_region(
        role_arn,
        home_region,
        lambda r: _iot_things_one_region(role_arn, r, external_id),
        "IoT",
        external_id=external_id,
    )


def _elbv2_one_region(role_arn: str, region: str, external_id: str | None = None) -> list[dict]:
    client = _create_assumed_client("elbv2", role_arn, region, external_id=external_id)
    out: list[dict] = []
    paginator = client.get_paginator("describe_load_balancers")
    for page in paginator.paginate():
        for lb in page.get("LoadBalancers", []) or []:
            lb_arn = str(lb.get("LoadBalancerArn") or "").strip()
            if not lb_arn:
                continue
            lb_name = str(lb.get("LoadBalancerName") or "").strip()

            tags: dict[str, str] = {}
            try:
                tag_resp = client.describe_tags(ResourceArns=[lb_arn])
                tag_rows = (tag_resp.get("TagDescriptions") or [{}])[0].get("Tags", [])
                tags = _normalize_tags(tag_rows)
            except (ClientError, BotoCoreError):
                tags = {}

            deletion_protection_enabled = False
            try:
                attrs_resp = client.describe_load_balancer_attributes(LoadBalancerArn=lb_arn)
                attrs = attrs_resp.get("Attributes", []) or []
                for a in attrs:
                    if str(a.get("Key") or "") == "deletion_protection.enabled":
                        deletion_protection_enabled = str(a.get("Value") or "").lower() == "true"
                        break
            except (ClientError, BotoCoreError):
                deletion_protection_enabled = False

            target_group_arns: list[str] = []
            healthy_target_count = 0
            try:
                tg_paginator = client.get_paginator("describe_target_groups")
                for tg_page in tg_paginator.paginate(LoadBalancerArn=lb_arn):
                    for tg in tg_page.get("TargetGroups", []) or []:
                        tg_arn = str(tg.get("TargetGroupArn") or "").strip()
                        if not tg_arn:
                            continue
                        target_group_arns.append(tg_arn)
                        try:
                            th = client.describe_target_health(TargetGroupArn=tg_arn)
                            for desc in th.get("TargetHealthDescriptions", []) or []:
                                state = str((desc.get("TargetHealth") or {}).get("State") or "").lower()
                                if state == "healthy":
                                    healthy_target_count += 1
                        except (ClientError, BotoCoreError):
                            continue
            except (ClientError, BotoCoreError):
                target_group_arns = []

            linked_resources = [
                _linked_resource_ref(
                    resource_type="target_group",
                    resource_id=tg_arn,
                    resource_name=tg_arn.rsplit(":", 1)[-1],
                    relation="routes_to",
                    confidence="direct_reference",
                    source="elbv2.describe_target_groups",
                )
                for tg_arn in target_group_arns
            ]

            out.append(
                _resource_envelope(
                    resource_id=lb_arn,
                    resource_type="load_balancer",
                    region=region,
                    configuration_json={
                        "load_balancer_name": lb_name,
                        "dns_name": lb.get("DNSName"),
                        "scheme": lb.get("Scheme"),
                        "type": lb.get("Type"),
                        "state": (lb.get("State") or {}).get("Code"),
                        "vpc_id": lb.get("VpcId"),
                        "ip_address_type": lb.get("IpAddressType"),
                        "availability_zone_count": len(lb.get("AvailabilityZones") or []),
                        "deletion_protection_enabled": deletion_protection_enabled,
                        "target_group_count": len(target_group_arns),
                        "healthy_target_count": healthy_target_count,
                        "linked_resources": linked_resources,
                    },
                    tags_json=tags,
                )
            )

    return out


def fetch_load_balancers(role_arn: str, home_region: str, external_id: str | None = None) -> list[dict]:
    return _gather_per_region(
        role_arn,
        home_region,
        lambda r: _elbv2_one_region(role_arn, r, external_id),
        "ELBv2",
        external_id=external_id,
    )


def _target_groups_one_region(role_arn: str, region: str, external_id: str | None = None) -> list[dict]:
    client = _create_assumed_client("elbv2", role_arn, region, external_id=external_id)
    out: list[dict] = []
    try:
        paginator = client.get_paginator("describe_target_groups")
        for page in paginator.paginate():
            for tg in page.get("TargetGroups", []) or []:
                tg_arn = str(tg.get("TargetGroupArn") or "").strip()
                if not tg_arn:
                    continue

                tg_name = str(tg.get("TargetGroupName") or "").strip()
                
                # Get health status for this target group
                healthy_count = 0
                unhealthy_count = 0
                total_count = 0
                try:
                    health_resp = client.describe_target_health(TargetGroupArn=tg_arn)
                    for target_health in health_resp.get("TargetHealthDescriptions", []) or []:
                        state = str((target_health.get("TargetHealth") or {}).get("State") or "").lower()
                        total_count += 1
                        if state == "healthy":
                            healthy_count += 1
                        elif state == "unhealthy":
                            unhealthy_count += 1
                except (ClientError, BotoCoreError):
                    pass
                
                # Get target group attributes (deregistration delay, stickiness, etc)
                attributes: dict[str, str] = {}
                try:
                    attrs_resp = client.describe_target_group_attributes(TargetGroupArn=tg_arn)
                    for attr in attrs_resp.get("Attributes", []) or []:
                        k = str(attr.get("Key") or "")
                        v = str(attr.get("Value") or "")
                        if k:
                            attributes[k] = v
                except (ClientError, BotoCoreError):
                    pass
                
                # Parse deregistration delay
                deregistration_delay_str = attributes.get("deregistration_delay.timeout_seconds", "30")
                try:
                    deregistration_delay = int(deregistration_delay_str)
                except (ValueError, TypeError):
                    deregistration_delay = 30
                
                # Parse stickiness
                stickiness_enabled = attributes.get("stickiness.enabled", "false").lower() == "true"
                stickiness_type = attributes.get("stickiness.type", "").lower()
                
                out.append(
                    _resource_envelope(
                        resource_id=tg_arn,
                        resource_type="target_group",
                        region=region,
                        configuration_json={
                            "target_group_name": tg_name,
                            "protocol": tg.get("Protocol"),
                            "port": tg.get("Port"),
                            "vpc_id": tg.get("VpcId"),
                            "target_type": tg.get("TargetType"),
                            "healthy_count": healthy_count,
                            "unhealthy_count": unhealthy_count,
                            "total_targets": total_count,
                            "health_check_enabled": tg.get("HealthCheckEnabled"),
                            "health_check_protocol": tg.get("HealthCheckProtocol"),
                            "health_check_path": tg.get("HealthCheckPath"),
                            "health_check_interval_seconds": tg.get("HealthCheckIntervalSeconds"),
                            "healthy_threshold_count": tg.get("HealthyThresholdCount"),
                            "unhealthy_threshold_count": tg.get("UnhealthyThresholdCount"),
                            "stickiness_enabled": stickiness_enabled,
                            "stickiness_type": stickiness_type,
                            "deregistration_delay_seconds": deregistration_delay,
                        },
                        tags_json=_normalize_tags(tg.get("Tags", [])),
                    )
                )
    except (ClientError, BotoCoreError) as e:
        if str(e.response.get("Error", {}).get("Code", "")) not in _SKIPPABLE:
            logger.warning("target_groups collection failed in %s: %s", region, e)
    
    return out


def fetch_target_groups(role_arn: str, home_region: str, external_id: str | None = None) -> list[dict]:
    return _gather_per_region(
        role_arn,
        home_region,
        lambda r: _target_groups_one_region(role_arn, r, external_id),
        "TargetGroups",
        external_id=external_id,
    )


def _route_tables_one_region(role_arn: str, region: str, external_id: str | None = None) -> list[dict]:
    ec2 = _create_assumed_client("ec2", role_arn, region, external_id=external_id)
    out: list[dict] = []
    paginator = ec2.get_paginator("describe_route_tables")
    for page in paginator.paginate():
        for rt in page.get("RouteTables", []) or []:
            rtid = rt.get("RouteTableId")
            if not rtid:
                continue

            associations = rt.get("Associations", []) or []
            routes = rt.get("Routes", []) or []
            has_any_default_route = False
            has_igw_default_route = False
            has_nat_default_route = False

            for route in routes:
                dest = str(route.get("DestinationCidrBlock") or route.get("DestinationIpv6CidrBlock") or "")
                if dest not in {"0.0.0.0/0", "::/0"}:
                    continue
                has_any_default_route = True
                gateway_id = str(route.get("GatewayId") or "")
                nat_gateway_id = str(route.get("NatGatewayId") or "")
                if gateway_id.startswith("igw-"):
                    has_igw_default_route = True
                if nat_gateway_id.startswith("nat-"):
                    has_nat_default_route = True

            linked_resources: list[dict[str, str]] = []
            for assoc in associations:
                subnet_id = str(assoc.get("SubnetId") or "").strip()
                if subnet_id:
                    linked_resources.append(
                        _linked_resource_ref(
                            resource_type="subnet",
                            resource_id=subnet_id,
                            resource_name=subnet_id,
                            relation="associated_subnet",
                            confidence="direct_reference",
                            source="ec2.describe_route_tables",
                        )
                    )

            out.append(
                _resource_envelope(
                    resource_id=rtid,
                    resource_type="route_table",
                    region=region,
                    configuration_json={
                        "vpc_id": rt.get("VpcId"),
                        "association_count": len(associations),
                        "route_count": len(routes),
                        "has_any_default_route": has_any_default_route,
                        "has_igw_default_route": has_igw_default_route,
                        "has_nat_default_route": has_nat_default_route,
                        "linked_resources": linked_resources,
                    },
                    tags_json=_normalize_tags(rt.get("Tags", [])),
                )
            )

    return out


def fetch_route_tables(role_arn: str, home_region: str, external_id: str | None = None) -> list[dict]:
    return _gather_per_region(
        role_arn,
        home_region,
        lambda r: _route_tables_one_region(role_arn, r, external_id),
        "RouteTables",
        external_id=external_id,
    )


def _ec2_vpcs_one_region(role_arn: str, region: str, external_id: str | None = None) -> list[dict]:
    ec2 = _create_assumed_client("ec2", role_arn, region, external_id=external_id)
    out: list[dict] = []
    paginator = ec2.get_paginator("describe_vpcs")
    for page in paginator.paginate():
        for vpc in page.get("Vpcs", []) or []:
            vid = vpc.get("VpcId")
            if not vid:
                continue
            out.append(
                {
                    "resource_id": vid,
                    "resource_type": "vpc",
                    "region": region,
                    "configuration_json": {
                        "cidr_block": vpc.get("CidrBlock"),
                        "is_default": vpc.get("IsDefault", False),
                    },
                    "tags_json": _normalize_tags(vpc.get("Tags", [])),
                }
            )
    return out


def _ec2_subnets_one_region(role_arn: str, region: str, external_id: str | None = None) -> list[dict]:
    ec2 = _create_assumed_client("ec2", role_arn, region, external_id=external_id)
    out: list[dict] = []
    paginator = ec2.get_paginator("describe_subnets")
    for page in paginator.paginate():
        for sn in page.get("Subnets", []) or []:
            sid = sn.get("SubnetId")
            if not sid:
                continue
            out.append(
                {
                    "resource_id": sid,
                    "resource_type": "subnet",
                    "region": region,
                    "configuration_json": {
                        "vpc_id": sn.get("VpcId"),
                        "cidr_block": sn.get("CidrBlock"),
                        "map_public_ip_on_launch": sn.get("MapPublicIpOnLaunch"),
                    },
                    "tags_json": _normalize_tags(sn.get("Tags", [])),
                }
            )
    return out


def _nat_one_region(role_arn: str, region: str, external_id: str | None = None) -> list[dict]:
    ec2 = _create_assumed_client("ec2", role_arn, region, external_id=external_id)
    out: list[dict] = []
    paginator = ec2.get_paginator("describe_nat_gateways")
    for page in paginator.paginate():
        for nat in page.get("NatGateways", []) or []:
            nid = nat.get("NatGatewayId")
            if not nid:
                continue
            out.append(
                {
                    "resource_id": nid,
                    "resource_type": "nat_gateway",
                    "region": region,
                    "configuration_json": {
                        "state": nat.get("State"),
                        "vpc_id": nat.get("VpcId"),
                        "subnet_id": nat.get("SubnetId"),
                    },
                    "tags_json": _normalize_tags(nat.get("Tags", [])),
                }
            )
    return out


def _igw_one_region(role_arn: str, region: str, external_id: str | None = None) -> list[dict]:
    ec2 = _create_assumed_client("ec2", role_arn, region, external_id=external_id)
    out: list[dict] = []
    paginator = ec2.get_paginator("describe_internet_gateways")
    for page in paginator.paginate():
        for igw in page.get("InternetGateways", []) or []:
            iid = igw.get("InternetGatewayId")
            if not iid:
                continue
            vpc_ids = [a.get("VpcId") for a in igw.get("Attachments", []) or [] if a.get("VpcId")]
            out.append(
                {
                    "resource_id": iid,
                    "resource_type": "internet_gateway",
                    "region": region,
                    "configuration_json": {"attached_vpc_ids": vpc_ids},
                    "tags_json": _normalize_tags(igw.get("Tags", [])),
                }
            )
    return out


def _sg_one_region(role_arn: str, region: str, external_id: str | None = None) -> list[dict]:
    ec2 = _create_assumed_client("ec2", role_arn, region, external_id=external_id)
    out: list[dict] = []
    n = 0
    paginator = ec2.get_paginator("describe_security_groups")
    for page in paginator.paginate():
        for sg in page.get("SecurityGroups", []) or []:
            sgid = sg.get("GroupId")
            if not sgid:
                continue
            n += 1
            if n > _MAX_SECURITY_GROUPS_PER_REGION:
                logger.warning(
                    "Security group inventory truncated at %d in %s",
                    _MAX_SECURITY_GROUPS_PER_REGION,
                    region,
                )
                return out
            permissions = sg.get("IpPermissions") or []
            has_world_open_ssh, has_world_open_rdp, has_world_open_all_ports = _summarize_world_open_ports(permissions)
            out.append(
                {
                    "resource_id": sgid,
                    "resource_type": "security_group",
                    "region": region,
                    "configuration_json": {
                        "vpc_id": sg.get("VpcId"),
                        "group_name": sg.get("GroupName"),
                        "ingress_rule_count": len(permissions),
                        "egress_rule_count": len(sg.get("IpPermissionsEgress") or []),
                        "has_world_open_ssh": has_world_open_ssh,
                        "has_world_open_rdp": has_world_open_rdp,
                        "has_world_open_all_ports": has_world_open_all_ports,
                    },
                    "tags_json": _normalize_tags(sg.get("Tags", [])),
                }
            )
    return out


def fetch_vpcs(role_arn: str, home_region: str, external_id: str | None = None) -> list[dict]:
    return _gather_per_region(
        role_arn,
        home_region,
        lambda r: _ec2_vpcs_one_region(role_arn, r, external_id),
        "VPC",
        external_id=external_id,
    )


def fetch_subnets(role_arn: str, home_region: str, external_id: str | None = None) -> list[dict]:
    return _gather_per_region(
        role_arn,
        home_region,
        lambda r: _ec2_subnets_one_region(role_arn, r, external_id),
        "subnet",
        external_id=external_id,
    )


def fetch_nat_gateways(role_arn: str, home_region: str, external_id: str | None = None) -> list[dict]:
    return _gather_per_region(
        role_arn,
        home_region,
        lambda r: _nat_one_region(role_arn, r, external_id),
        "NAT GW",
        external_id=external_id,
    )


def fetch_internet_gateways(role_arn: str, home_region: str, external_id: str | None = None) -> list[dict]:
    return _gather_per_region(
        role_arn,
        home_region,
        lambda r: _igw_one_region(role_arn, r, external_id),
        "IGW",
        external_id=external_id,
    )


def fetch_security_groups(role_arn: str, home_region: str, external_id: str | None = None) -> list[dict]:
    return _gather_per_region(
        role_arn,
        home_region,
        lambda r: _sg_one_region(role_arn, r, external_id),
        "SG",
        external_id=external_id,
    )


def _rds_parameter_groups_one_region(role_arn: str, region: str, external_id: str | None = None) -> list[dict]:
    rds = _create_assumed_client("rds", role_arn, region, external_id=external_id)
    out: list[dict] = []
    try:
        paginator = rds.get_paginator("describe_db_parameter_groups")
        for page in paginator.paginate():
            for pg in page.get("DBParameterGroups", []) or []:
                pg_name = str(pg.get("DBParameterGroupName") or "").strip()
                pg_arn = str(pg.get("DBParameterGroupArn") or "").strip()
                
                if not pg_arn:
                    continue
                
                # Get tags for this parameter group
                tags: dict[str, str] = {}
                try:
                    tags_resp = rds.list_tags_for_resource(ResourceName=pg_arn)
                    for tag_list in tags_resp.get("TagList", []) or []:
                        k = str(tag_list.get("Key") or "").strip()
                        v = str(tag_list.get("Value") or "").strip()
                        if k:
                            tags[k] = v
                except (ClientError, BotoCoreError):
                    tags = {}
                
                # Get parameter details (slow query logging, query insights, etc)
                slow_query_log_enabled = False
                general_log_enabled = False
                log_bin_trust_function_creators = False
                
                try:
                    params_resp = rds.describe_db_parameters(
                        DBParameterGroupName=pg_name,
                        Filters=[
                            {"Name": "source", "Values": ["user"]},
                        ],
                        MaxRecords=100,
                    )
                    user_params = params_resp.get("Parameters", []) or []
                    
                    for param in user_params:
                        pname = str(param.get("ParameterName") or "").lower()
                        if pname == "slow_query_log":
                            slow_query_log_enabled = str(param.get("ParameterValue") or "").lower() in ("1", "true")
                        elif pname == "general_log":
                            general_log_enabled = str(param.get("ParameterValue") or "").lower() in ("1", "true")
                        elif pname == "log_bin_trust_function_creators":
                            log_bin_trust_function_creators = str(param.get("ParameterValue") or "").lower() in ("1", "true")
                except (ClientError, BotoCoreError):
                    user_params = []
                
                out.append(
                    _resource_envelope(
                        resource_id=pg_arn,
                        resource_type="rds_parameter_group",
                        region=region,
                        configuration_json={
                            "parameter_group_name": pg_name,
                            "db_parameter_group_family": pg.get("DBParameterGroupFamily"),
                            "description": pg.get("Description"),
                            "parameter_group_status": pg.get("DBParameterGroupStatus"),
                            "slow_query_log_enabled": slow_query_log_enabled,
                            "general_log_enabled": general_log_enabled,
                            "log_bin_trust_function_creators": log_bin_trust_function_creators,
                            "custom_parameter_count": len(user_params),
                        },
                        tags_json=tags,
                    )
                )
    except (ClientError, BotoCoreError) as e:
        if str(e.response.get("Error", {}).get("Code", "")) not in _SKIPPABLE:
            logger.warning("rds_parameter_groups collection failed in %s: %s", region, e)
    
    return out


def fetch_rds_parameter_groups(role_arn: str, home_region: str, external_id: str | None = None) -> list[dict]:
    return _gather_per_region(
        role_arn,
        home_region,
        lambda r: _rds_parameter_groups_one_region(role_arn, r, external_id),
        "RDSParameterGroups",
        external_id=external_id,
    )


def fetch_all_extended(role_arn: str, home_region: str, external_id: str | None = None) -> dict[str, list[dict]]:
    """Return keyed batches for observability (each value is a list of snapshot dicts)."""
    batches = {
        "cloudfront_distribution": fetch_cloudfront_distributions(role_arn, home_region, external_id),
        "acm_certificate": fetch_acm_certificates(role_arn, home_region, external_id),
        "apigateway_rest_api": fetch_apigateway_rest_apis(role_arn, home_region, external_id),
        "apigateway_http_api": fetch_apigateway_http_apis(role_arn, home_region, external_id),
        "eventbridge_rule": fetch_eventbridge_rules(role_arn, home_region, external_id),
        "ses_email_identity": fetch_ses_email_identities(role_arn, home_region, external_id),
        "iot_thing": fetch_iot_things(role_arn, home_region, external_id),
        "load_balancer": fetch_load_balancers(role_arn, home_region, external_id),
        "target_group": fetch_target_groups(role_arn, home_region, external_id),
        "route_table": fetch_route_tables(role_arn, home_region, external_id),
        "vpc": fetch_vpcs(role_arn, home_region, external_id),
        "subnet": fetch_subnets(role_arn, home_region, external_id),
        "nat_gateway": fetch_nat_gateways(role_arn, home_region, external_id),
        "internet_gateway": fetch_internet_gateways(role_arn, home_region, external_id),
        "security_group": fetch_security_groups(role_arn, home_region, external_id),
        "rds_parameter_group": fetch_rds_parameter_groups(role_arn, home_region, external_id),
    }
    _link_cloudfront_to_acm(batches)
    return batches


def _link_cloudfront_to_acm(batches: dict[str, list[dict]]) -> None:
    cloudfront_rows = batches.get("cloudfront_distribution") or []
    acm_rows = batches.get("acm_certificate") or []
    if not cloudfront_rows or not acm_rows:
        return
    acm_by_arn: dict[str, dict] = {}
    acm_domains: dict[str, list[dict]] = {}
    for row in acm_rows:
        rid = str(row.get("resource_id") or "")
        cfg = row.get("configuration_json") or {}
        if rid:
            acm_by_arn[rid] = row
        for dom in [cfg.get("domain_name"), *(cfg.get("subject_alternative_names") or [])]:
            d = str(dom or "").strip().lower()
            if d:
                acm_domains.setdefault(d, []).append(row)
    for cf in cloudfront_rows:
        cfg = cf.get("configuration_json") or {}
        linked: list[dict] = list(cfg.get("linked_resources") or [])
        arn = str(cfg.get("viewer_certificate_acm_arn") or "").strip()
        matched: dict | None = acm_by_arn.get(arn) if arn else None
        confidence = "unknown"
        source = "none"
        if matched is not None:
            confidence = "direct_arn_match"
            source = "cloudfront.viewer_certificate.acm_arn"
        else:
            candidates: list[dict] = []
            for d in [cfg.get("domain_name"), *(cfg.get("aliases") or [])]:
                key = str(d or "").strip().lower()
                if not key:
                    continue
                candidates.extend(acm_domains.get(key, []))
            # high-confidence fallback only when exactly one unique certificate matches domains/aliases
            uniq = {str(x.get("resource_id")): x for x in candidates}
            if len(uniq) == 1:
                matched = next(iter(uniq.values()))
                confidence = "domain_match"
                source = "cloudfront.domain_or_alias_to_acm_domain"
        if matched is not None:
            mcfg = matched.get("configuration_json") or {}
            cfg["linked_acm_certificate_arn"] = str(matched.get("resource_id") or "")
            cfg["linked_acm_certificate_domain"] = str(mcfg.get("domain_name") or "")
            cfg["linked_acm_certificate_status"] = str(mcfg.get("status") or "")
            cfg["link_confidence"] = confidence
            linked.append(
                _linked_resource_ref(
                    resource_type="acm_certificate",
                    resource_id=cfg["linked_acm_certificate_arn"],
                    resource_name=cfg["linked_acm_certificate_domain"] or cfg["linked_acm_certificate_arn"],
                    relation="uses_certificate",
                    confidence=confidence,
                    source=source,
                )
            )
            # reverse "used_by_distribution" on ACM snapshot
            rev_cfg = matched.setdefault("configuration_json", {})
            rev_links = list(rev_cfg.get("linked_resources") or [])
            rev_links.append(
                _linked_resource_ref(
                    resource_type="cloudfront_distribution",
                    resource_id=str(cf.get("resource_id") or ""),
                    resource_name=str(cfg.get("domain_name") or ""),
                    relation="used_by_distribution",
                    confidence=confidence,
                    source=source,
                )
            )
            rev_cfg["linked_resources"] = rev_links
        else:
            cfg["link_confidence"] = "unknown"
        cfg["linked_resources"] = linked
