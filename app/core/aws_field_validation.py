"""Format validation for AWS onboarding fields (account ID, role ARN, region)."""

from __future__ import annotations

import re

_AWS_ACCOUNT_ID = re.compile(r"^\d{12}$")
# Role ARN: arn:aws:iam::<12 digits>:role/<path...>
_ROLE_ARN = re.compile(r"^arn:aws:iam::(\d{12}):role/.+$")


def normalize_aws_account_id(value: str) -> str:
    """Strip whitespace; remove common separators users paste from consoles."""
    return (value or "").strip().replace("-", "").replace(" ", "")


def validate_aws_account_id(account_id: str) -> str:
    normalized = normalize_aws_account_id(account_id)
    if not _AWS_ACCOUNT_ID.match(normalized):
        raise ValueError("AWS account ID must be exactly 12 digits.")
    return normalized


def validate_role_arn(role_arn: str) -> str:
    t = (role_arn or "").strip()
    if not t.startswith("arn:aws:iam::"):
        raise ValueError("Role ARN must be an IAM role ARN (arn:aws:iam::account:role/...).")
    if not _ROLE_ARN.match(t):
        raise ValueError(
            "Role ARN format is invalid. Expected arn:aws:iam::<12-digit-account>:role/<role-name>."
        )
    return t


def account_id_from_role_arn(role_arn: str) -> str | None:
    m = _ROLE_ARN.match((role_arn or "").strip())
    return m.group(1) if m else None


def validate_region_default(region: str) -> str:
    r = (region or "").strip()
    if not r:
        raise ValueError("Default region is required.")
    rl = r.lower()
    if len(rl) > 64 or not re.match(r"^[a-z0-9-]+$", rl):
        raise ValueError("Region must use lowercase letters, digits, and hyphens only (max 64 characters).")
    return rl
