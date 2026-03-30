"""Deterministic pricing helpers for cost optimization findings."""

from decimal import Decimal

from app.core.cost_window import round_currency_decimal


RDS_INSTANCE_MONTHLY_PRICE_ESTIMATES = {
    "db.t3.medium": Decimal("50.00"),
    "db.t3.large": Decimal("100.00"),
    "db.t4g.medium": Decimal("40.00"),
    "db.t4g.large": Decimal("80.00"),
    "db.m5.large": Decimal("140.00"),
    "db.m5.xlarge": Decimal("280.00"),
    "db.r5.large": Decimal("180.00"),
    "db.r5.xlarge": Decimal("360.00"),
}
RDS_INSTANCE_RIGHTSIZE_SAVINGS_RATIO = Decimal("0.35")

AURORA_SERVERLESS_MONTHLY_PRICE_ESTIMATES = {
    "aurora-mysql": Decimal("80.00"),
    "aurora-postgresql": Decimal("90.00"),
    "default": Decimal("85.00"),
}
AURORA_SERVERLESS_REVIEW_SAVINGS_RATIO = Decimal("0.30")

LAMBDA_MONTHLY_PRICE_ESTIMATES = {
    "128": Decimal("5.00"),
    "512": Decimal("18.00"),
    "1024": Decimal("35.00"),
    "default": Decimal("12.00"),
}
LAMBDA_OPTIMIZATION_SAVINGS_RATIO = Decimal("0.25")


def _to_currency(value: Decimal) -> Decimal:
    return round_currency_decimal(value)


def estimate_rds_instance_monthly_savings(evidence_json: dict) -> Decimal | None:
    """Estimate monthly savings for supported RDS instance class findings."""
    db_instance_class = evidence_json.get("db_instance_class")
    monthly_price = RDS_INSTANCE_MONTHLY_PRICE_ESTIMATES.get(db_instance_class)
    if monthly_price is None:
        return None

    return _to_currency(monthly_price * RDS_INSTANCE_RIGHTSIZE_SAVINGS_RATIO)


def estimate_aurora_serverless_monthly_savings(evidence_json: dict) -> Decimal | None:
    """Estimate monthly savings for Aurora Serverless review findings."""
    engine = evidence_json.get("engine") or "default"
    monthly_price = AURORA_SERVERLESS_MONTHLY_PRICE_ESTIMATES.get(
        engine,
        AURORA_SERVERLESS_MONTHLY_PRICE_ESTIMATES["default"],
    )
    return _to_currency(monthly_price * AURORA_SERVERLESS_REVIEW_SAVINGS_RATIO)


def estimate_lambda_monthly_savings(evidence_json: dict) -> Decimal | None:
    """Estimate monthly savings for future Lambda cost findings."""
    memory_size = str(evidence_json.get("memory_size") or "default")
    monthly_price = LAMBDA_MONTHLY_PRICE_ESTIMATES.get(memory_size, LAMBDA_MONTHLY_PRICE_ESTIMATES["default"])
    return _to_currency(monthly_price * LAMBDA_OPTIMIZATION_SAVINGS_RATIO)


def estimate_monthly_savings_for_finding(
    finding_type: str,
    evidence_json: dict,
    resource_type: str,
) -> Decimal | None:
    """Return deterministic monthly savings estimate for supported finding types."""
    if resource_type == "rds_instance" and finding_type == "rds_instance_overprovisioned":
        return estimate_rds_instance_monthly_savings(evidence_json)

    if finding_type == "aurora_serverless_review_candidate" and resource_type in (
        "rds_instance",
        "aurora_cluster",
    ):
        return estimate_aurora_serverless_monthly_savings(evidence_json)

    if resource_type == "lambda_function":
        return estimate_lambda_monthly_savings(evidence_json)

    return None
