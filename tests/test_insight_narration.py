"""Insight narration: deterministic text from summary + trends + outcomes."""

from app.models.cloud_account import CloudAccount
from app.models.tenant import Tenant
from app.services.insight_narration_service import generate_insight_summary


def test_insight_summary_empty_when_no_cost_data_and_no_recommendations(db, dev_org_scope):
    """No Cost Explorer snapshot + no recs/outcomes → skip boilerplate executive copy."""
    org = dev_org_scope["org"]
    tenant = Tenant(name="insight-empty-tenant", organization_id=org.id)
    db.add(tenant)
    db.flush()
    cloud_account = CloudAccount(
        tenant_id=tenant.id,
        account_id="999888777666",
        name="empty-insights-account",
        role_arn="arn:aws:iam::999888777666:role/ReadOnly",
        region_default="us-east-1",
    )
    db.add(cloud_account)
    db.commit()

    out = generate_insight_summary(db, tenant.id, cloud_account.id)

    assert out["summary_text"] == ""
    assert out["cost_basis_note"] == ""
    assert out["cost_window_label"]
