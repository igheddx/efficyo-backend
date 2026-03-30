"""Manual end-to-end flow for CloudAccount validation and EC2 ingestion.

Usage:
  BASE_URL=http://127.0.0.1:8000 /Users/oplyft/Documents/Development/Application/fptnext/.venv/bin/python scripts/manual_test_flow.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from uuid import uuid4

import httpx


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
API_PREFIX = "/api/v1"


def _safe_json(response: httpx.Response):
    try:
        return response.json()
    except Exception:
        return {"raw_body": response.text}


def _print_step(title: str, payload: dict) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(payload, indent=2, default=str))


def _build_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}{API_PREFIX}{path}"


def create_tenant(client: httpx.Client, base_url: str, tenant_name: str) -> dict:
    url = _build_url(base_url, "/tenants")
    body = {"name": tenant_name}

    response = client.post(url, json=body, timeout=30.0)
    if response.status_code == 201:
        data = _safe_json(response)
        _print_step("Create Tenant", {"status_code": response.status_code, "response": data})
        return data

    if response.status_code == 409:
        retry_name = f"{tenant_name}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6]}"
        retry_body = {"name": retry_name}
        retry_response = client.post(url, json=retry_body, timeout=30.0)
        if retry_response.status_code == 201:
            data = _safe_json(retry_response)
            _print_step(
                "Create Tenant (Retry with unique name)",
                {
                    "status_code": retry_response.status_code,
                    "retry_name": retry_name,
                    "response": data,
                },
            )
            return data

        _print_step(
            "Create Tenant Failed (Retry)",
            {
                "status_code": retry_response.status_code,
                "response": _safe_json(retry_response),
            },
        )
        raise RuntimeError("Tenant creation retry failed")

    _print_step(
        "Create Tenant Failed",
        {
            "status_code": response.status_code,
            "response": _safe_json(response),
        },
    )
    raise RuntimeError("Tenant creation failed")


def create_cloud_account(client: httpx.Client, base_url: str, tenant_id: str) -> dict:
    url = _build_url(base_url, f"/tenants/{tenant_id}/cloud-accounts")
    body = {
        "account_id": "393795841779",
        "name": "Tipwave AWS",
        "role_arn": "arn:aws:iam::393795841779:role/FptNextReadOnlyRole",
        "region_default": "us-east-1",
    }

    response = client.post(url, json=body, timeout=30.0)
    if response.status_code == 201:
        data = _safe_json(response)
        _print_step("Create Cloud Account", {"status_code": response.status_code, "response": data})
        return data

    _print_step(
        "Create Cloud Account Failed",
        {
            "status_code": response.status_code,
            "response": _safe_json(response),
        },
    )
    raise RuntimeError("Cloud account creation failed")


def validate_cloud_account(client: httpx.Client, base_url: str, tenant_id: str, cloud_account_id: str) -> dict:
    url = _build_url(base_url, f"/tenants/{tenant_id}/cloud-accounts/{cloud_account_id}/validate")
    response = client.post(url, timeout=60.0)

    if response.status_code == 200:
        data = _safe_json(response)
        _print_step("Validate Cloud Account", {"status_code": response.status_code, "response": data})
        return data

    _print_step(
        "Validate Cloud Account Failed",
        {
            "status_code": response.status_code,
            "response": _safe_json(response),
        },
    )
    raise RuntimeError("Cloud account validation failed")


def ingest_ec2(client: httpx.Client, base_url: str, tenant_id: str, cloud_account_id: str) -> dict:
    url = _build_url(base_url, f"/tenants/{tenant_id}/cloud-accounts/{cloud_account_id}/ingest/ec2")
    response = client.post(url, timeout=120.0)

    if response.status_code == 200:
        data = _safe_json(response)
        _print_step("Ingest EC2", {"status_code": response.status_code, "response": data})
        return data

    _print_step(
        "Ingest EC2 Failed",
        {
            "status_code": response.status_code,
            "response": _safe_json(response),
        },
    )
    raise RuntimeError("EC2 ingestion failed")


def ingest_rds(client: httpx.Client, base_url: str, tenant_id: str, cloud_account_id: str) -> dict:
    url = _build_url(base_url, f"/tenants/{tenant_id}/cloud-accounts/{cloud_account_id}/ingest/rds")
    response = client.post(url, timeout=120.0)

    if response.status_code == 200:
        data = _safe_json(response)
        _print_step("Ingest RDS", {"status_code": response.status_code, "response": data})
        return data

    _print_step(
        "Ingest RDS Failed",
        {
            "status_code": response.status_code,
            "response": _safe_json(response),
        },
    )
    raise RuntimeError("RDS ingestion failed")


def detect_rds(client: httpx.Client, base_url: str, tenant_id: str, cloud_account_id: str) -> dict:
    url = _build_url(base_url, f"/tenants/{tenant_id}/cloud-accounts/{cloud_account_id}/detect/rds")
    response = client.post(url, timeout=120.0)

    if response.status_code == 200:
        data = _safe_json(response)
        _print_step("Detect RDS", {"status_code": response.status_code, "response": data})
        return data

    _print_step(
        "Detect RDS Failed",
        {
            "status_code": response.status_code,
            "response": _safe_json(response),
        },
    )
    raise RuntimeError("RDS detection failed")


def recommend_rds(client: httpx.Client, base_url: str, tenant_id: str, cloud_account_id: str) -> dict:
    url = _build_url(base_url, f"/tenants/{tenant_id}/cloud-accounts/{cloud_account_id}/recommend/rds")
    response = client.post(url, timeout=120.0)

    if response.status_code == 200:
        data = _safe_json(response)
        _print_step("Recommend RDS", {"status_code": response.status_code, "response": data})
        return data

    _print_step(
        "Recommend RDS Failed",
        {
            "status_code": response.status_code,
            "response": _safe_json(response),
        },
    )
    raise RuntimeError("RDS recommendation generation failed")


def get_recommendations(client: httpx.Client, base_url: str, tenant_id: str, cloud_account_id: str) -> dict | list:
    url = _build_url(base_url, f"/tenants/{tenant_id}/cloud-accounts/{cloud_account_id}/recommendations")
    response = client.get(url, timeout=120.0)

    data = _safe_json(response)
    _print_step("Get Recommendations", {"status_code": response.status_code, "response": data})
    return data


def get_findings(client: httpx.Client, base_url: str, tenant_id: str, cloud_account_id: str) -> dict | list:
    url = _build_url(base_url, f"/tenants/{tenant_id}/cloud-accounts/{cloud_account_id}/findings")
    response = client.get(url, timeout=120.0)

    data = _safe_json(response)
    _print_step("Get Findings", {"status_code": response.status_code, "response": data})
    return data


def main() -> int:
    base_url = os.getenv("BASE_URL", DEFAULT_BASE_URL)
    tenant_name = f"tipwave-manual-{datetime.utcnow().strftime('%Y%m%d')}"

    print("Starting manual cloud account validation + EC2 ingestion flow")
    print(f"Base URL: {base_url}")

    try:
        with httpx.Client() as client:
            tenant = create_tenant(client, base_url, tenant_name)
            cloud_account = create_cloud_account(client, base_url, str(tenant["id"]))
            validate_cloud_account(client, base_url, str(tenant["id"]), str(cloud_account["id"]))
            ingest_ec2(client, base_url, str(tenant["id"]), str(cloud_account["id"]))
            ingest_rds(client, base_url, str(tenant["id"]), str(cloud_account["id"]))
            detect_rds(client, base_url, str(tenant["id"]), str(cloud_account["id"]))
            recommend_rds(client, base_url, str(tenant["id"]), str(cloud_account["id"]))
            get_recommendations(client, base_url, str(tenant["id"]), str(cloud_account["id"]))
            get_findings(client, base_url, str(tenant["id"]), str(cloud_account["id"]))
    except Exception as exc:
        print(f"\nFlow failed: {exc}")
        return 1

    print("\nFlow completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
