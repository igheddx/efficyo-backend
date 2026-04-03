"""AWS Cost Explorer service for recent spend summaries."""

from datetime import date
from decimal import Decimal
import logging

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.core.cost_window import (
    DEFAULT_COST_METRIC,
    account_cost_window_fields,
    account_default_ce_period,
    ce_rolling_period_days,
    ce_time_period,
    ce_metrics,
    round_currency,
)
from app.core.demo_aws import is_tipwave_demo_role_arn
from app.services import aws_assume_role_service

logger = logging.getLogger(__name__)

_COST_EXPLORER_REGION = "us-east-1"
_EC2_OTHER_SERVICE_NAME = "EC2 - Other"

# IMPORTANT COST CONTROL:
# This module contains raw AWS Cost Explorer calls and must only be invoked via
# app.cost.client.CostExplorerClient. UI/API GET flows must never call this directly.


def _empty_cost_summary(start: date, end: date) -> dict:
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "total_cost": 0.0,
        "by_service": [],
        **account_cost_window_fields(),
    }


def _demo_role_aws_fallback(role_arn: str, exc: BaseException) -> bool:
    """Seeded Tipwave uses a placeholder IAM role; STS/Cost Explorer often fail — return empty data, not 5xx."""
    if not is_tipwave_demo_role_arn(role_arn):
        return False
    logger.warning(
        "AWS STS/Cost Explorer unavailable for Tipwave demo role; using empty spend data: %s",
        exc,
    )
    return True


def fetch_daily_unblended_cost_by_service(
    role_arn: str,
    days: int = 14,
    external_id: str | None = None,
) -> list[dict]:
    """
    Last ``days`` of unblended cost per day per service (Cost Explorer DAILY + SERVICE group by).

    Each item: ``{"date": "YYYY-MM-DD", "by_service": {service_name: Decimal}}``.
    Days are sorted ascending by date. Missing days are omitted (no spend that day).
    """
    window_days = max(1, int(days))
    start_date, end_date = ce_rolling_period_days(window_days)

    try:
        credentials = aws_assume_role_service.assume_role(
            role_arn=role_arn,
            region=_COST_EXPLORER_REGION,
            session_name=f"fptnext-cost-explorer-daily-{window_days}",
            external_id=external_id,
        )

        ce_client = boto3.client(
            "ce",
            region_name=_COST_EXPLORER_REGION,
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
        )

        merged_by_day: dict[str, dict[str, Decimal]] = {}
        next_token: str | None = None

        while True:
            params: dict = {
                "TimePeriod": ce_time_period(start_date, end_date),
                "Granularity": "DAILY",
                "Metrics": ce_metrics(),
                "GroupBy": [{"Type": "DIMENSION", "Key": "SERVICE"}],
            }
            if next_token:
                params["NextPageToken"] = next_token

            response = ce_client.get_cost_and_usage(**params)

            for period in response.get("ResultsByTime", []):
                day_start = (period.get("TimePeriod") or {}).get("Start")
                if not day_start:
                    continue
                if day_start not in merged_by_day:
                    merged_by_day[day_start] = {}
                bucket = merged_by_day[day_start]
                for group in period.get("Groups", []):
                    service_name = (group.get("Keys") or ["Unknown"])[0] or "Unknown"
                    amount_str = group.get("Metrics", {}).get(DEFAULT_COST_METRIC, {}).get("Amount", "0")
                    amount = Decimal(amount_str)
                    bucket[service_name] = bucket.get(service_name, Decimal("0.00")) + amount

            next_token = response.get("NextPageToken")
            if not next_token:
                break

        ordered_dates = sorted(merged_by_day.keys())
        return [{"date": d, "by_service": merged_by_day[d]} for d in ordered_dates]

    except Exception as exc:
        if _demo_role_aws_fallback(role_arn, exc):
            return []
        if isinstance(exc, ClientError):
            error_code = exc.response["Error"]["Code"]
            error_msg = exc.response["Error"]["Message"]
            logger.error(
                f"AWS Cost Explorer daily-by-service error for role {role_arn}: {error_code} - {error_msg}"
            )
        elif isinstance(exc, BotoCoreError):
            logger.error(f"BotoCore error fetching daily Cost Explorer data for role {role_arn}: {str(exc)}")
        raise


def fetch_daily_unblended_cost_by_service_last_14_days(
    role_arn: str,
    external_id: str | None = None,
) -> list[dict]:
    return fetch_daily_unblended_cost_by_service(role_arn=role_arn, days=14, external_id=external_id)


def rolling_30d_unblended_account_total_decimal(role_arn: str, external_id: str | None = None) -> Decimal | None:
    """
    Total UnblendedCost for the same rolling 30d window as ``fetch_cost_summary`` (account-wide headline total).

    Used for proof-of-savings before/after snapshots so numbers align with dashboard cost context.
    """
    try:
        summary = fetch_cost_summary(role_arn, external_id=external_id)
        return Decimal(str(summary.get("total_cost", 0)))
    except Exception:
        logger.exception("rolling_30d_unblended_account_total_decimal failed")
        return None


def fetch_cost_summary(role_arn: str, external_id: str | None = None) -> dict:
    """Fetch rolling account-window unblended AWS cost grouped by service (UTC dates, same as EC2-Other)."""
    start_date, end_date = account_default_ce_period()

    try:
        credentials = aws_assume_role_service.assume_role(
            role_arn=role_arn,
            region=_COST_EXPLORER_REGION,
            session_name="fptnext-cost-explorer",
            external_id=external_id,
        )

        ce_client = boto3.client(
            "ce",
            region_name=_COST_EXPLORER_REGION,
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
        )

        cost_by_service: dict[str, Decimal] = {}
        total_cost = Decimal("0.00")
        next_token: str | None = None
        time_period = ce_time_period(start_date, end_date)

        while True:
            params: dict = {
                "TimePeriod": time_period,
                "Granularity": "DAILY",
                "Metrics": ce_metrics(),
                "GroupBy": [{"Type": "DIMENSION", "Key": "SERVICE"}],
            }
            if next_token:
                params["NextPageToken"] = next_token

            response = ce_client.get_cost_and_usage(**params)

            for period in response.get("ResultsByTime", []):
                for group in period.get("Groups", []):
                    service_name = (group.get("Keys") or ["Unknown"])[0] or "Unknown"
                    amount_str = group.get("Metrics", {}).get(DEFAULT_COST_METRIC, {}).get("Amount", "0")
                    amount = Decimal(amount_str)
                    total_cost += amount
                    cost_by_service[service_name] = cost_by_service.get(service_name, Decimal("0.00")) + amount

            next_token = response.get("NextPageToken")
            if not next_token:
                break

        by_service = [
            {"service": service_name, "amount": round_currency(amount)}
            for service_name, amount in sorted(
                cost_by_service.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ]

        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "total_cost": round_currency(total_cost),
            "by_service": by_service,
            **account_cost_window_fields(),
        }

    except Exception as exc:
        if _demo_role_aws_fallback(role_arn, exc):
            return _empty_cost_summary(start_date, end_date)
        if isinstance(exc, ClientError):
            error_code = exc.response["Error"]["Code"]
            error_msg = exc.response["Error"]["Message"]
            logger.error(f"AWS Cost Explorer error for role {role_arn}: {error_code} - {error_msg}")
        elif isinstance(exc, BotoCoreError):
            logger.error(f"BotoCore error fetching Cost Explorer data for role {role_arn}: {str(exc)}")
        raise


def fetch_aws_waf_monthly_cost(role_arn: str, external_id: str | None = None) -> float:
    """Last-30-days unblended AWS WAF spend from the same cost-by-service data as ``fetch_cost_summary``."""
    summary = fetch_cost_summary(role_arn, external_id=external_id)
    total = Decimal("0.00")
    for item in summary.get("by_service", []):
        name = (item.get("service") or "").strip()
        if name == "AWS WAF" or name.startswith("AWS WAF "):
            total += Decimal(str(item.get("amount", 0.0)))
    return round_currency(total)


def _categorize_ec2_other_usage_type(usage_type: str) -> str:
    usage_type_lower = usage_type.lower()

    if "natgateway" in usage_type_lower:
        return "NAT Gateway"
    if "ebs:snapshot" in usage_type_lower or "snapshot" in usage_type_lower:
        return "EBS Snapshots"
    if "datatransfer" in usage_type_lower:
        return "Data Transfer"
    return "Other"


def fetch_ec2_other_breakdown(role_arn: str, external_id: str | None = None) -> dict:
    """EC2-Other unblended cost by usage category for the same window as headline account cost."""
    start_date, end_date = account_default_ce_period()

    try:
        credentials = aws_assume_role_service.assume_role(
            role_arn=role_arn,
            region=_COST_EXPLORER_REGION,
            session_name="fptnext-cost-explorer",
            external_id=external_id,
        )

        ce_client = boto3.client(
            "ce",
            region_name=_COST_EXPLORER_REGION,
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
        )

        response = ce_client.get_cost_and_usage(
            TimePeriod=ce_time_period(start_date, end_date),
            Granularity="DAILY",
            Metrics=ce_metrics(),
            GroupBy=[{"Type": "DIMENSION", "Key": "USAGE_TYPE"}],
            Filter={
                "Dimensions": {
                    "Key": "SERVICE",
                    "Values": [_EC2_OTHER_SERVICE_NAME],
                }
            },
        )

        category_totals: dict[str, Decimal] = {
            "NAT Gateway": Decimal("0.00"),
            "EBS Snapshots": Decimal("0.00"),
            "Data Transfer": Decimal("0.00"),
            "Other": Decimal("0.00"),
        }

        for period in response.get("ResultsByTime", []):
            for group in period.get("Groups", []):
                usage_type = (group.get("Keys") or [""])[0] or ""
                amount_str = group.get("Metrics", {}).get(DEFAULT_COST_METRIC, {}).get("Amount", "0")
                amount = Decimal(amount_str)
                category = _categorize_ec2_other_usage_type(usage_type)
                category_totals[category] += amount

        groups_count = sum(len(period.get("Groups", [])) for period in response.get("ResultsByTime", []))
        if groups_count == 0:
            logger.info(
                "EC2-Other breakdown returned no groups",
                extra={
                    "role_arn": role_arn,
                    "service_filter": _EC2_OTHER_SERVICE_NAME,
                    "results_by_time": response.get("ResultsByTime", []),
                },
            )

        breakdown = [
            {"category": category, "amount": round_currency(amount)}
            for category, amount in category_totals.items()
            if amount > Decimal("0")
        ]

        breakdown.sort(key=lambda item: (-item["amount"], item["category"]))
        ec2_other_total = sum(category_totals.values(), Decimal("0.00"))

        return {
            "ec2_other_total": round_currency(ec2_other_total),
            "breakdown": breakdown,
        }

    except Exception as exc:
        if _demo_role_aws_fallback(role_arn, exc):
            return {"ec2_other_total": 0.0, "breakdown": []}
        if isinstance(exc, ClientError):
            error_code = exc.response["Error"]["Code"]
            error_msg = exc.response["Error"]["Message"]
            logger.error(f"AWS Cost Explorer EC2-Other error for role {role_arn}: {error_code} - {error_msg}")
        elif isinstance(exc, BotoCoreError):
            logger.error(f"BotoCore error fetching EC2-Other Cost Explorer data for role {role_arn}: {str(exc)}")
        raise
