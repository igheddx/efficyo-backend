"""Notifications API: list, mark read, mark all (org + user scoped)."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy.orm import Session


def test_list_and_mark_read(client, db: Session, dev_org_scope_admin):
    from app.models.notification import Notification

    org = dev_org_scope_admin["org"]
    user = dev_org_scope_admin["user"]
    headers = dev_org_scope_admin["headers"]

    n = Notification(
        user_id=user.id,
        organization_id=org.id,
        type="sync_completed",
        message="Test sync done",
        entity_type="ingestion_job",
        entity_id=uuid4(),
        payload={"view": "dashboard"},
        is_read=False,
    )
    db.add(n)
    db.commit()
    db.refresh(n)

    r = client.get("/api/v1/notifications", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["unread_count"] >= 1
    assert any(item["id"] == str(n.id) for item in data["items"])

    r2 = client.get("/api/v1/notifications?read=false", headers=headers)
    assert r2.status_code == 200
    assert all(not item["is_read"] for item in r2.json()["items"])

    r3 = client.post(f"/api/v1/notifications/{n.id}/read", headers=headers)
    assert r3.status_code == 200
    assert r3.json()["is_read"] is True

    r4 = client.get("/api/v1/notifications", headers=headers)
    assert r4.json()["unread_count"] == 0


def test_mark_all_read(client, db: Session, dev_org_scope_admin):
    from app.models.notification import Notification

    org = dev_org_scope_admin["org"]
    user = dev_org_scope_admin["user"]
    headers = dev_org_scope_admin["headers"]

    for i in range(2):
        db.add(
            Notification(
                user_id=user.id,
                organization_id=org.id,
                type="approval_required",
                message=f"Pending {i}",
                is_read=False,
            )
        )
    db.commit()

    r = client.post("/api/v1/notifications/read-all", headers=headers)
    assert r.status_code == 200
    assert r.json()["updated"] == 2

    r2 = client.get("/api/v1/notifications?read=false", headers=headers)
    assert r2.json()["total"] == 0
