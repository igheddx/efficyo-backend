"""Ingestion sync job duplicate prevention and execution."""

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models.cloud_account import CloudAccount
from app.models.ingestion_job import IngestionJob
from app.models.tenant import Tenant
from app.services.ingestion_job_service import ActiveSyncJobExists, create_sync_job, execute_sync_job


class _SessionNoClose:
    """Wrap a test Session so execute_sync_job's SessionLocal() does not close the shared fixture."""

    __slots__ = ("_s",)

    def __init__(self, session: Session) -> None:
        self._s = session

    def __getattr__(self, name: str):
        return getattr(self._s, name)

    def close(self) -> None:
        pass


@pytest.fixture
def tenant_and_cloud(db: Session):
    t = Tenant(name=f"tenant-{uuid4()}")
    db.add(t)
    db.commit()
    db.refresh(t)
    ca = CloudAccount(
        tenant_id=t.id,
        account_id="123456789012",
        name="acct",
        status="active",
        role_arn="arn:aws:iam::123456789012:role/x",
        region_default="us-east-1",
    )
    db.add(ca)
    db.commit()
    db.refresh(ca)
    return t, ca


def test_create_sync_job_blocks_when_queued_exists(db: Session, tenant_and_cloud):
    tenant, cloud = tenant_and_cloud
    j1 = create_sync_job(db, tenant.id, cloud.id, "full_sync")
    assert j1.status == "queued"
    with pytest.raises(ActiveSyncJobExists) as exc_info:
        create_sync_job(db, tenant.id, cloud.id, "full_sync")
    assert exc_info.value.job.id == j1.id


def test_create_sync_job_allows_after_completed(db: Session, tenant_and_cloud):
    tenant, cloud = tenant_and_cloud
    j1 = create_sync_job(db, tenant.id, cloud.id, "full_sync")
    j1.status = "completed"
    j1.completed_at = j1.updated_at
    db.add(j1)
    db.commit()
    j2 = create_sync_job(db, tenant.id, cloud.id, "full_sync")
    assert j2.id != j1.id
    assert j2.status == "queued"


def test_execute_marks_failed_with_message(db: Session, tenant_and_cloud, monkeypatch):
    tenant, cloud = tenant_and_cloud
    job = create_sync_job(db, tenant.id, cloud.id, "full_sync")

    def boom(*_args, **_kwargs):
        raise RuntimeError("pipeline boom")

    monkeypatch.setattr(
        "app.services.ingestion_job_service._run_sync_pipeline",
        boom,
    )
    monkeypatch.setattr(
        "app.services.ingestion_job_service.SessionLocal",
        lambda: _SessionNoClose(db),
    )
    execute_sync_job(job.id)
    db.expire_all()
    row = db.query(IngestionJob).filter(IngestionJob.id == job.id).first()
    assert row.status == "failed"
    assert row.error_message and "boom" in row.error_message
    assert row.started_at is not None
    assert row.completed_at is not None
    assert row.updated_at is not None


def test_post_sync_conflict_returns_409_with_active_job(client, db, dev_org_scope):
    org = dev_org_scope["org"]
    h = dev_org_scope["headers"]
    t = Tenant(name=f"sync-api-{uuid4()}", organization_id=org.id)
    db.add(t)
    db.commit()
    db.refresh(t)
    ca = CloudAccount(
        tenant_id=t.id,
        account_id="123456789012",
        name="acct",
        status="active",
        role_arn="arn:aws:iam::123456789012:role/x",
        region_default="us-east-1",
    )
    db.add(ca)
    db.commit()
    db.refresh(ca)
    existing = IngestionJob(
        tenant_id=t.id,
        cloud_account_id=ca.id,
        job_type="full_sync",
        status="queued",
    )
    db.add(existing)
    db.commit()
    db.refresh(existing)

    res = client.post(
        f"/api/v1/tenants/{t.id}/cloud-accounts/{ca.id}/sync",
        json={"job_type": "full_sync"},
        headers=h,
    )
    assert res.status_code == 409
    payload = res.json()["detail"]
    assert payload["message"]
    assert payload["active_job"]["id"] == str(existing.id)
    assert payload["active_job"]["status"] == "queued"


def test_get_sync_job_returns_expected_fields(client, db, dev_org_scope):
    org = dev_org_scope["org"]
    h = dev_org_scope["headers"]
    t = Tenant(name=f"sync-get-{uuid4()}", organization_id=org.id)
    db.add(t)
    db.commit()
    db.refresh(t)
    ca = CloudAccount(
        tenant_id=t.id,
        account_id="123456789012",
        name="acct",
        status="active",
        role_arn="arn:aws:iam::123456789012:role/x",
        region_default="us-east-1",
    )
    db.add(ca)
    db.commit()
    db.refresh(ca)
    job = IngestionJob(
        tenant_id=t.id,
        cloud_account_id=ca.id,
        job_type="full_sync",
        status="failed",
        error_message="something broke",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    res = client.get(
        f"/api/v1/tenants/{t.id}/cloud-accounts/{ca.id}/sync-jobs/{job.id}",
        headers=h,
    )
    assert res.status_code == 200
    data = res.json()
    for key in (
        "id",
        "job_type",
        "status",
        "started_at",
        "completed_at",
        "error_message",
        "created_at",
        "updated_at",
    ):
        assert key in data
    assert data["status"] == "failed"
    assert "broke" in (data.get("error_message") or "")
