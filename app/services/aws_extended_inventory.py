"""
Extended AWS discovery for CloudFront, ACM, API Gateway, EventBridge, SES, and VPC networking.

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


def fetch_cloudfront_distributions(role_arn: str, home_region: str) -> list[dict]:
    """Global CloudFront distributions (API region us-east-1)."""
    region = "us-east-1"
    out: list[dict] = []
    try:
        cf = _create_assumed_client("cloudfront", role_arn, region)
        sts = _create_assumed_client("sts", role_arn, home_region)
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


def _acm_one_region(role_arn: str, region: str) -> list[dict]:
    acm = _create_assumed_client("acm", role_arn, region)
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


def fetch_acm_certificates(role_arn: str, home_region: str) -> list[dict]:
    return _gather_per_region(role_arn, home_region, lambda r: _acm_one_region(role_arn, r), "ACM")


def _apigw_rest_one_region(role_arn: str, region: str) -> list[dict]:
    client = _create_assumed_client("apigateway", role_arn, region)
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
        cfg = {"name": api.get("name"), "created_date": str(api.get("createdDate", ""))}
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


def fetch_apigateway_rest_apis(role_arn: str, home_region: str) -> list[dict]:
    return _gather_per_region(role_arn, home_region, lambda r: _apigw_rest_one_region(role_arn, r), "API Gateway REST")


def _apigw_http_one_region(role_arn: str, region: str) -> list[dict]:
    client = _create_assumed_client("apigatewayv2", role_arn, region)
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


def fetch_apigateway_http_apis(role_arn: str, home_region: str) -> list[dict]:
    return _gather_per_region(role_arn, home_region, lambda r: _apigw_http_one_region(role_arn, r), "API Gateway HTTP")


def _events_rules_one_region(role_arn: str, region: str) -> list[dict]:
    client = _create_assumed_client("events", role_arn, region)
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
            }
            bus = cfg.get("event_bus_name") or "default"
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


def fetch_eventbridge_rules(role_arn: str, home_region: str) -> list[dict]:
    return _gather_per_region(role_arn, home_region, lambda r: _events_rules_one_region(role_arn, r), "EventBridge")


def _ses_identities_one_region(role_arn: str, region: str) -> list[dict]:
    client = _create_assumed_client("sesv2", role_arn, region)
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


def fetch_ses_email_identities(role_arn: str, home_region: str) -> list[dict]:
    return _gather_per_region(role_arn, home_region, lambda r: _ses_identities_one_region(role_arn, r), "SES")


def _ec2_vpcs_one_region(role_arn: str, region: str) -> list[dict]:
    ec2 = _create_assumed_client("ec2", role_arn, region)
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


def _ec2_subnets_one_region(role_arn: str, region: str) -> list[dict]:
    ec2 = _create_assumed_client("ec2", role_arn, region)
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


def _nat_one_region(role_arn: str, region: str) -> list[dict]:
    ec2 = _create_assumed_client("ec2", role_arn, region)
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


def _igw_one_region(role_arn: str, region: str) -> list[dict]:
    ec2 = _create_assumed_client("ec2", role_arn, region)
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


def _sg_one_region(role_arn: str, region: str) -> list[dict]:
    ec2 = _create_assumed_client("ec2", role_arn, region)
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
            out.append(
                {
                    "resource_id": sgid,
                    "resource_type": "security_group",
                    "region": region,
                    "configuration_json": {
                        "vpc_id": sg.get("VpcId"),
                        "group_name": sg.get("GroupName"),
                        "ingress_rule_count": len(sg.get("IpPermissions") or []),
                        "egress_rule_count": len(sg.get("IpPermissionsEgress") or []),
                    },
                    "tags_json": _normalize_tags(sg.get("Tags", [])),
                }
            )
    return out


def fetch_vpcs(role_arn: str, home_region: str) -> list[dict]:
    return _gather_per_region(role_arn, home_region, lambda r: _ec2_vpcs_one_region(role_arn, r), "VPC")


def fetch_subnets(role_arn: str, home_region: str) -> list[dict]:
    return _gather_per_region(role_arn, home_region, lambda r: _ec2_subnets_one_region(role_arn, r), "subnet")


def fetch_nat_gateways(role_arn: str, home_region: str) -> list[dict]:
    return _gather_per_region(role_arn, home_region, lambda r: _nat_one_region(role_arn, r), "NAT GW")


def fetch_internet_gateways(role_arn: str, home_region: str) -> list[dict]:
    return _gather_per_region(role_arn, home_region, lambda r: _igw_one_region(role_arn, r), "IGW")


def fetch_security_groups(role_arn: str, home_region: str) -> list[dict]:
    return _gather_per_region(role_arn, home_region, lambda r: _sg_one_region(role_arn, r), "SG")


def fetch_all_extended(role_arn: str, home_region: str) -> dict[str, list[dict]]:
    """Return keyed batches for observability (each value is a list of snapshot dicts)."""
    batches = {
        "cloudfront_distribution": fetch_cloudfront_distributions(role_arn, home_region),
        "acm_certificate": fetch_acm_certificates(role_arn, home_region),
        "apigateway_rest_api": fetch_apigateway_rest_apis(role_arn, home_region),
        "apigateway_http_api": fetch_apigateway_http_apis(role_arn, home_region),
        "eventbridge_rule": fetch_eventbridge_rules(role_arn, home_region),
        "ses_email_identity": fetch_ses_email_identities(role_arn, home_region),
        "vpc": fetch_vpcs(role_arn, home_region),
        "subnet": fetch_subnets(role_arn, home_region),
        "nat_gateway": fetch_nat_gateways(role_arn, home_region),
        "internet_gateway": fetch_internet_gateways(role_arn, home_region),
        "security_group": fetch_security_groups(role_arn, home_region),
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
