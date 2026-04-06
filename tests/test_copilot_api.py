"""Operations Copilot API (rules-based path when OpenAI key unset)."""

from uuid import uuid4

from app.models.cloud_account import CloudAccount
from app.models.tenant import Tenant


def test_copilot_query_returns_structured_response(client, db, dev_org_scope, monkeypatch):
    monkeypatch.setattr(
        "app.services.cost_explorer_service.fetch_cost_summary",
        lambda role_arn: {
            "start_date": "2026-02-23",
            "end_date": "2026-03-25",
            "total_cost": 200.0,
            "by_service": [{"service": "Amazon EC2", "amount": 200.0}],
            "cost_window": "rolling_30d",
            "cost_window_label": "Last 30 days",
            "cost_metric": "UnblendedCost",
        },
    )
    monkeypatch.setattr(
        "app.services.trend_service.detect_cost_trends",
        lambda _db, _tid, _cid: [],
    )

    org = dev_org_scope["org"]
    tenant = Tenant(name="copilot-tenant", organization_id=org.id)
    db.add(tenant)
    db.flush()
    cloud = CloudAccount(
        tenant_id=tenant.id,
        account_id="123456789012",
        name="copilot-account",
        role_arn="arn:aws:iam::123456789012:role/OptimizationRole",
        region_default="us-east-1",
        connection_status="valid",
    )
    db.add(cloud)
    db.commit()

    res = client.post(
        "/api/v1/copilot/query",
        headers=dev_org_scope["headers"],
        json={
            "query": "What should I focus on today?",
            "organization_id": str(org.id),
            "tenant_id": str(tenant.id),
            "cloud_account_id": str(cloud.id),
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert "answer" in data and len(data["answer"]) > 0
    assert "priority_actions" in data and isinstance(data["priority_actions"], list)
    assert "insights" in data and isinstance(data["insights"], list)
    assert "suggested_next_steps" not in data
    # structured field must always be present (null for LLM path, object for rules/fallback path)
    assert "structured" in data
    if data.get("structured") is not None:
        s = data["structured"]
        assert "response_type" in s
        assert "sections" in s
        assert "summary" in s
        assert "meta" in s
    if data.get("debug") is not None:
        assert data["debug"]["detected_intent"] == "prioritize"
        assert isinstance(data["debug"]["context_item_count"], int)
        if "items_sent_to_llm" in data["debug"]:
            assert isinstance(data["debug"]["items_sent_to_llm"], int)
        if data["debug"].get("grouping_counts") is not None:
            assert isinstance(data["debug"]["grouping_counts"], dict)
    for act in data.get("priority_actions") or []:
        assert "next_step" in act
        assert "action_type" in act


def test_copilot_query_rejects_wrong_organization(client, db, dev_org_scope, monkeypatch):
    monkeypatch.setattr(
        "app.services.cost_explorer_service.fetch_cost_summary",
        lambda role_arn: {
            "start_date": "2026-02-23",
            "end_date": "2026-03-25",
            "total_cost": 1.0,
            "by_service": [],
            "cost_window": "rolling_30d",
            "cost_window_label": "Last 30 days",
            "cost_metric": "UnblendedCost",
        },
    )
    monkeypatch.setattr("app.services.trend_service.detect_cost_trends", lambda *_a, **_k: [])

    org = dev_org_scope["org"]
    tenant = Tenant(name="copilot-tenant-2", organization_id=org.id)
    db.add(tenant)
    db.flush()
    cloud = CloudAccount(
        tenant_id=tenant.id,
        account_id="123456789012",
        name="copilot-account-2",
        role_arn="arn:aws:iam::123456789012:role/OptimizationRole",
        region_default="us-east-1",
    )
    db.add(cloud)
    db.commit()

    res = client.post(
        "/api/v1/copilot/query",
        headers=dev_org_scope["headers"],
        json={
            "query": "Hello",
            "organization_id": str(uuid4()),
            "tenant_id": str(tenant.id),
            "cloud_account_id": str(cloud.id),
        },
    )
    assert res.status_code == 400


def test_copilot_low_priority_skips_llm(client, db, dev_org_scope, monkeypatch):
    llm_packs: list = []

    def capture_llm(pack):
        llm_packs.append(pack)
        return None

    monkeypatch.setattr("app.services.copilot_service._call_openai_with_pack", capture_llm)
    monkeypatch.setattr(
        "app.services.cost_explorer_service.fetch_cost_summary",
        lambda role_arn: {
            "start_date": "2026-02-23",
            "end_date": "2026-03-25",
            "total_cost": 1.0,
            "by_service": [],
            "cost_window": "rolling_30d",
            "cost_window_label": "Last 30 days",
            "cost_metric": "UnblendedCost",
        },
    )
    monkeypatch.setattr("app.services.trend_service.detect_cost_trends", lambda *_a, **_k: [])

    org = dev_org_scope["org"]
    tenant = Tenant(name="copilot-tenant-lp", organization_id=org.id)
    db.add(tenant)
    db.flush()
    cloud = CloudAccount(
        tenant_id=tenant.id,
        account_id="123456789012",
        name="copilot-account-lp",
        role_arn="arn:aws:iam::123456789012:role/OptimizationRole",
        region_default="us-east-1",
        connection_status="valid",
    )
    db.add(cloud)
    db.commit()

    res = client.post(
        "/api/v1/copilot/query",
        headers=dev_org_scope["headers"],
        json={
            "query": "What is safe to defer?",
            "organization_id": str(org.id),
            "tenant_id": str(tenant.id),
            "cloud_account_id": str(cloud.id),
        },
    )
    assert res.status_code == 200, res.text
    assert llm_packs == []
    data = res.json()
    assert data["priority_actions"] == []
    assert data["insights"] == []
    assert "Summary" in data["answer"] and "Low Priority Items" in data["answer"]
    # structured JSON contract must be present for low_priority intent
    assert data.get("structured") is not None, "low_priority must return structured response"
    structured = data["structured"]
    assert structured["response_type"] == "low_priority"
    assert isinstance(structured["sections"], list)
    assert isinstance(structured["summary"]["counts"], dict)
    assert structured["meta"]["version"] == "1"
