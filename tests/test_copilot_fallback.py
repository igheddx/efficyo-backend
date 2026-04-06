"""Tests for copilot fallback detection and response building.

Coverage:
 - classify_query_feasibility for all four non-supported statuses
 - build_fallback_response shapes and content
 - API integration: unsupported + empty-result queries return fallback field
"""

from uuid import uuid4

import pytest

from app.models.cloud_account import CloudAccount
from app.models.tenant import Tenant
from app.schemas.ai_response import FallbackAIResponse
from app.services.copilot_fallback_service import (
    _SUPPORTED_AREAS,
    build_fallback_response,
    classify_query_feasibility,
)


# ── Unit: classify_query_feasibility ──────────────────────────────────────────


def test_classify_supported_query_with_data():
    scoped = {"scored_attention_top": [{"title": "Fix something", "action_type": "investigate"}]}
    result = classify_query_feasibility("What should I focus on today?", "prioritize", scoped)
    assert result == "supported"


def test_classify_unsupported_coding_query():
    scoped = {"scored_attention_top": [{"title": "item"}]}
    result = classify_query_feasibility("write code for a lambda function", "general_summary", scoped)
    assert result == "unsupported"


def test_classify_unsupported_weather_query():
    scoped = {"scored_attention_top": [{"title": "item"}]}
    result = classify_query_feasibility("what is the weather today?", "general_summary", scoped)
    assert result == "unsupported"


def test_classify_unsupported_off_domain():
    result = classify_query_feasibility("tell me a joke", "general_summary", {})
    assert result == "unsupported"


def test_classify_ambiguous_single_word():
    result = classify_query_feasibility("hi", "general_summary", {"scored_attention_top": [{"title": "x"}]})
    assert result == "ambiguous"


def test_classify_ambiguous_short_query():
    # Single word that isn't in exact set but still 1-word → ambiguous
    result = classify_query_feasibility("help", "general_summary", {"scored_attention_top": [{"title": "x"}]})
    assert result == "ambiguous"


def test_classify_ambiguous_one_real_word():
    # "ok" is in the exact ambiguous set
    result = classify_query_feasibility("ok", "general_summary", {"scored_attention_top": [{"title": "x"}]})
    assert result == "ambiguous"


def test_classify_no_data_empty_scoped_prioritize():
    result = classify_query_feasibility(
        "What should I focus on today?",
        "prioritize",
        {},
    )
    assert result == "no_data"


def test_classify_no_data_empty_approvals():
    result = classify_query_feasibility(
        "Show pending approvals",
        "approvals",
        {"pending_approvals": []},
    )
    assert result == "no_data"


def test_classify_no_data_empty_executions():
    result = classify_query_feasibility(
        "What can I run now?",
        "executions",
        {"ready_to_execute": [], "not_ready_to_execute": []},
    )
    assert result == "no_data"


def test_classify_no_data_empty_savings():
    result = classify_query_feasibility(
        "Show cost savings",
        "savings",
        {"savings_proof_account": {}, "savings_proof_tenant": {}, "top_opportunities": []},
    )
    assert result == "no_data"


def test_classify_no_data_general_summary_error():
    result = classify_query_feasibility(
        "Give me an overview",
        "general_summary",
        {"summary": {"error": "connection timeout"}},
    )
    assert result == "no_data"


def test_classify_no_data_tenants_empty():
    result = classify_query_feasibility(
        "Which tenants need attention?",
        "tenants",
        {"tenant_directory": []},
    )
    assert result == "no_data"


# ── Unit: build_fallback_response ─────────────────────────────────────────────


def test_build_fallback_unsupported_shape():
    fb = build_fallback_response("unsupported", "write a bash script", "general_summary")
    assert isinstance(fb, FallbackAIResponse)
    assert fb.response_type == "unsupported"
    assert len(fb.message) > 0
    assert len(fb.reason) > 0
    assert isinstance(fb.suggestions, list)
    assert isinstance(fb.supported_areas, list)


def test_build_fallback_no_data_response_type():
    fb = build_fallback_response("no_data", "show approvals", "approvals")
    assert fb.response_type == "empty_result"
    assert len(fb.suggestions) > 0


def test_build_fallback_ambiguous_response_type():
    fb = build_fallback_response("ambiguous", "hi", "general_summary")
    assert fb.response_type == "clarification_needed"


def test_build_fallback_system_limitation_response_type():
    fb = build_fallback_response("system_limitation", "some query", "prioritize")
    assert fb.response_type == "limitation"


def test_build_fallback_suggestions_max_5():
    for status in ("unsupported", "no_data", "ambiguous", "system_limitation"):
        fb = build_fallback_response(status, "test query", "prioritize")
        assert len(fb.suggestions) <= 5, f"status={status} has >5 suggestions"


def test_build_fallback_supported_areas_populated():
    fb = build_fallback_response("unsupported", "tell me a joke", "general_summary")
    assert len(fb.supported_areas) > 0
    assert fb.supported_areas == list(_SUPPORTED_AREAS)


def test_build_fallback_no_data_suggestions_point_elsewhere():
    # no_data suggestions should point toward alternate areas with potential data
    fb = build_fallback_response("no_data", "show approvals", "approvals")
    # Should NOT just repeat the same intent — should offer diversified suggestions
    assert any("low priority" in s.lower() or "savings" in s.lower() or "recommendations" in s.lower()
               for s in fb.suggestions)


# ── Integration: API returns fallback field ────────────────────────────────────


def _setup_tenant_and_account(db, org):
    """Helper: create tenant + cloud account for copilot tests."""
    tenant = Tenant(
        name=f"fb-tenant-{uuid4().hex[:6]}",
        organization_id=org.id,
    )
    db.add(tenant)
    db.flush()
    cloud = CloudAccount(
        tenant_id=tenant.id,
        account_id="999999999999",
        name=f"fb-account-{uuid4().hex[:6]}",
        role_arn="arn:aws:iam::999999999999:role/OptimizationRole",
        region_default="us-east-1",
        connection_status="valid",
    )
    db.add(cloud)
    db.commit()
    return tenant, cloud


def _patch_cost(monkeypatch):
    monkeypatch.setattr(
        "app.services.cost_explorer_service.fetch_cost_summary",
        lambda role_arn: {
            "start_date": "2026-02-23",
            "end_date": "2026-03-25",
            "total_cost": 0.0,
            "by_service": [],
            "cost_window": "rolling_30d",
            "cost_window_label": "Last 30 days",
            "cost_metric": "UnblendedCost",
        },
    )
    monkeypatch.setattr("app.services.trend_service.detect_cost_trends", lambda *_a, **_k: [])


def test_api_unsupported_query_returns_fallback(client, db, dev_org_scope, monkeypatch):
    """Queries outside the MEEZI domain return a fallback with response_type=unsupported."""
    _patch_cost(monkeypatch)
    org = dev_org_scope["org"]
    tenant, cloud = _setup_tenant_and_account(db, org)

    res = client.post(
        "/api/v1/copilot/query",
        headers=dev_org_scope["headers"],
        json={
            "query": "write code for a terraform resource",
            "organization_id": str(org.id),
            "tenant_id": str(tenant.id),
            "cloud_account_id": str(cloud.id),
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data.get("fallback") is not None, "Expected fallback field for unsupported query"
    fb = data["fallback"]
    assert fb["response_type"] == "unsupported"
    assert len(fb["message"]) > 0
    assert len(fb["reason"]) > 0
    assert isinstance(fb["suggestions"], list) and 1 <= len(fb["suggestions"]) <= 5
    assert isinstance(fb["supported_areas"], list) and len(fb["supported_areas"]) > 0
    # structured should be absent / null for fallback responses
    assert data.get("structured") is None


def test_api_empty_result_query_returns_fallback(client, db, dev_org_scope, monkeypatch):
    """Supported intent with no data (empty account) returns empty_result fallback."""
    _patch_cost(monkeypatch)
    org = dev_org_scope["org"]
    tenant, cloud = _setup_tenant_and_account(db, org)

    res = client.post(
        "/api/v1/copilot/query",
        headers=dev_org_scope["headers"],
        json={
            "query": "Show pending approvals",
            "organization_id": str(org.id),
            "tenant_id": str(tenant.id),
            "cloud_account_id": str(cloud.id),
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data.get("fallback") is not None, "Expected fallback for empty approvals query"
    fb = data["fallback"]
    assert fb["response_type"] == "empty_result"
    assert isinstance(fb["suggestions"], list)


def test_api_ambiguous_query_returns_fallback(client, db, dev_org_scope, monkeypatch):
    """Single-word or overly generic queries return clarification_needed fallback."""
    _patch_cost(monkeypatch)
    org = dev_org_scope["org"]
    tenant, cloud = _setup_tenant_and_account(db, org)

    res = client.post(
        "/api/v1/copilot/query",
        headers=dev_org_scope["headers"],
        json={
            "query": "hello",
            "organization_id": str(org.id),
            "tenant_id": str(tenant.id),
            "cloud_account_id": str(cloud.id),
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data.get("fallback") is not None, "Expected fallback for ambiguous query"
    fb = data["fallback"]
    assert fb["response_type"] == "clarification_needed"
    assert isinstance(fb["suggestions"], list) and len(fb["suggestions"]) > 0


def test_api_normal_query_has_no_fallback(client, db, dev_org_scope, monkeypatch):
    """A normal, valid query with available data must NOT return a fallback."""
    monkeypatch.setattr(
        "app.services.cost_explorer_service.fetch_cost_summary",
        lambda role_arn: {
            "start_date": "2026-02-23",
            "end_date": "2026-03-25",
            "total_cost": 500.0,
            "by_service": [{"service": "Amazon EC2", "amount": 500.0}],
            "cost_window": "rolling_30d",
            "cost_window_label": "Last 30 days",
            "cost_metric": "UnblendedCost",
        },
    )
    monkeypatch.setattr("app.services.trend_service.detect_cost_trends", lambda *_a, **_k: [])

    org = dev_org_scope["org"]
    tenant, cloud = _setup_tenant_and_account(db, org)

    res = client.post(
        "/api/v1/copilot/query",
        headers=dev_org_scope["headers"],
        json={
            "query": "Show cost savings opportunities",
            "organization_id": str(org.id),
            "tenant_id": str(tenant.id),
            "cloud_account_id": str(cloud.id),
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    # Fallback must be absent (null) for a reachable savings query
    assert data.get("fallback") is None, f"Unexpected fallback on normal query: {data.get('fallback')}"
