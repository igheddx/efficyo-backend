"""Stable demo identifiers shared by DB seed and AWS Cost Explorer fallbacks."""

# Tipwave seed: role used by fptnext for this cloud account (trust policy must allow your caller).
TIPWAVE_DEMO_AWS_ACCOUNT_ID = "393795841779"
TIPWAVE_DEMO_ROLE_ARN = f"arn:aws:iam::{TIPWAVE_DEMO_AWS_ACCOUNT_ID}:role/FptNextReadOnlyRole"

# Older seeds / names; migrate rows to TIPWAVE_DEMO_ROLE_ARN. Still match for empty CE fallback.
LEGACY_TIPWAVE_DEMO_ROLE_ARN = "arn:aws:iam::123456789012:role/OptimizationRole"
LEGACY_TIPWAVE_OPTIMIZATION_ROLE_ARN = f"arn:aws:iam::{TIPWAVE_DEMO_AWS_ACCOUNT_ID}:role/OptimizationRole"


def is_tipwave_demo_role_arn(role_arn: str | None) -> bool:
    if not role_arn:
        return False
    r = role_arn.strip()
    return r in (
        TIPWAVE_DEMO_ROLE_ARN,
        LEGACY_TIPWAVE_DEMO_ROLE_ARN,
        LEGACY_TIPWAVE_OPTIMIZATION_ROLE_ARN,
    )
