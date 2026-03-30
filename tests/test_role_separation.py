from uuid import uuid4

from app.models.tenant import Tenant


def test_approve_requires_approver_family_role(client, db, dev_org_scope):
    org = dev_org_scope["org"]
    t = Tenant(name="role-sep-a", organization_id=org.id)
    db.add(t)
    db.commit()
    db.refresh(t)
    cloud_account_id = uuid4()
    recommendation_id = uuid4()
    # Viewer operational access cannot approve (admin membership maps to full ops; use viewer header).
    h = {**dev_org_scope["headers"], "X-User": "alice", "X-Role": "viewer"}
    response = client.post(
        f"/api/v1/tenants/{t.id}/cloud-accounts/{cloud_account_id}/recommendations/{recommendation_id}/approve",
        json={"approved_by": "alice"},
        headers=h,
    )
    assert response.status_code == 403


def test_execute_requires_admin_family_role(client, db, dev_org_scope):
    org = dev_org_scope["org"]
    t = Tenant(name="role-sep-b", organization_id=org.id)
    db.add(t)
    db.commit()
    db.refresh(t)
    cloud_account_id = uuid4()
    recommendation_id = uuid4()
    h = {**dev_org_scope["headers"], "X-User": "bob", "X-Role": "approver"}
    response = client.post(
        f"/api/v1/tenants/{t.id}/cloud-accounts/{cloud_account_id}/recommendations/{recommendation_id}/execute",
        json={"executed_by": "ops-user"},
        headers=h,
    )
    assert response.status_code == 403

