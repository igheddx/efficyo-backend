"""Tests for notification Phase 4: scheduling, policy, snooze, retry."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.notification_delivery_log import NotificationDeliveryLog
from app.models.notification_policy import NotificationPolicy
from app.models.notification_schedule import NotificationSchedule
from app.models.notification_snooze import NotificationSnooze
from app.models.org_integration import OrgIntegration
from app.services import (
    notification_policy_service,
    notification_schedule_service,
    notification_snooze_service,
)
from app.services.notification_retry_service import (
    MAX_RETRIES,
    _BACKOFF_SECONDS,
    backoff_seconds,
    get_retryable_logs,
    schedule_retry,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_org(db: Session):
    from app.models.organization import Organization

    org = Organization(name=f"Ph4-org-{uuid4().hex[:8]}", slug=f"ph4-{uuid4().hex[:8]}", status="active")
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _log(db: Session, org_id, provider="slack", status="failed", retry_count=0):
    row = NotificationDeliveryLog(
        organization_id=org_id,
        provider=provider,
        event_type="top_actions",
        target_type="org",
        target_key=str(org_id),
        route_kind="org_channel",
        dedupe_key=f"test-{uuid4().hex}",
        status=status,
        retry_count=retry_count,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ─────────────────────────────────────────────────────────────────────────────
# notification_policy_service
# ─────────────────────────────────────────────────────────────────────────────

class TestPolicyService:
    def test_get_or_create_creates_defaults(self, db: Session):
        org = _make_org(db)
        policy = notification_policy_service.get_or_create_policy(db, org.id)
        assert policy.organization_id == org.id
        assert policy.min_priority == "low"
        assert policy.throttle_window_minutes == 60
        assert policy.digest_mode == "instant"

    def test_get_or_create_idempotent(self, db: Session):
        org = _make_org(db)
        p1 = notification_policy_service.get_or_create_policy(db, org.id)
        p2 = notification_policy_service.get_or_create_policy(db, org.id)
        assert p1.id == p2.id

    def test_update_policy(self, db: Session):
        org = _make_org(db)
        policy = notification_policy_service.update_policy(
            db, org.id, {"min_priority": "high", "throttle_window_minutes": 120}
        )
        assert policy.min_priority == "high"
        assert policy.throttle_window_minutes == 120

    def test_passes_priority_filter_low_allows_all(self, db: Session):
        org = _make_org(db)
        policy = notification_policy_service.get_or_create_policy(db, org.id)
        policy.min_priority = "low"
        for p in ("low", "medium", "high"):
            assert notification_policy_service.passes_priority_filter(policy, p) is True

    def test_passes_priority_filter_high_blocks_low_medium(self, db: Session):
        org = _make_org(db)
        policy = notification_policy_service.get_or_create_policy(db, org.id)
        policy.min_priority = "high"
        assert notification_policy_service.passes_priority_filter(policy, "low") is False
        assert notification_policy_service.passes_priority_filter(policy, "medium") is False
        assert notification_policy_service.passes_priority_filter(policy, "high") is True

    def test_passes_priority_filter_medium(self, db: Session):
        org = _make_org(db)
        policy = notification_policy_service.get_or_create_policy(db, org.id)
        policy.min_priority = "medium"
        assert notification_policy_service.passes_priority_filter(policy, "low") is False
        assert notification_policy_service.passes_priority_filter(policy, "medium") is True
        assert notification_policy_service.passes_priority_filter(policy, "high") is True

    def test_passes_event_filter_none_allows_all(self, db: Session):
        org = _make_org(db)
        policy = notification_policy_service.get_or_create_policy(db, org.id)
        policy.enabled_event_types = None
        for et in ("top_actions", "critical_alert", "approval_pending", "execution_failed"):
            assert notification_policy_service.passes_event_filter(policy, et) is True

    def test_passes_event_filter_allowlist(self, db: Session):
        org = _make_org(db)
        policy = notification_policy_service.get_or_create_policy(db, org.id)
        policy.enabled_event_types = ["critical_alert", "approval_pending"]
        assert notification_policy_service.passes_event_filter(policy, "critical_alert") is True
        assert notification_policy_service.passes_event_filter(policy, "approval_pending") is True
        assert notification_policy_service.passes_event_filter(policy, "top_actions") is False
        assert notification_policy_service.passes_event_filter(policy, "execution_failed") is False


# ─────────────────────────────────────────────────────────────────────────────
# notification_schedule_service
# ─────────────────────────────────────────────────────────────────────────────

class TestScheduleService:
    def test_upsert_schedule_creates(self, db: Session):
        org = _make_org(db)
        sched = notification_schedule_service.upsert_schedule(
            db, org.id, frequency="daily", time_of_day="09:00"
        )
        assert sched.organization_id == org.id
        assert sched.frequency == "daily"
        assert sched.time_of_day == "09:00"
        assert sched.next_run_at is not None

    def test_upsert_schedule_updates(self, db: Session):
        org = _make_org(db)
        notification_schedule_service.upsert_schedule(db, org.id, frequency="daily")
        updated = notification_schedule_service.upsert_schedule(
            db, org.id, frequency="weekly", day_of_week=2, time_of_day="11:00"
        )
        assert updated.frequency == "weekly"
        assert updated.day_of_week == 2

    def test_compute_next_run_daily_future(self):
        now = datetime(2024, 6, 1, 8, 0, 0, tzinfo=timezone.utc)  # 08:00
        nxt = notification_schedule_service.compute_next_run(
            frequency="daily", day_of_week=None, time_of_day="09:00", after=now
        )
        assert nxt == datetime(2024, 6, 1, 9, 0, 0, tzinfo=timezone.utc)

    def test_compute_next_run_daily_past_rolls_to_tomorrow(self):
        now = datetime(2024, 6, 1, 10, 0, 0, tzinfo=timezone.utc)  # already after 09:00
        nxt = notification_schedule_service.compute_next_run(
            frequency="daily", day_of_week=None, time_of_day="09:00", after=now
        )
        assert nxt == datetime(2024, 6, 2, 9, 0, 0, tzinfo=timezone.utc)

    def test_compute_next_run_weekly(self):
        # 2024-06-01 is Saturday (weekday=5). Next Monday (0) should be 2024-06-03.
        now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        nxt = notification_schedule_service.compute_next_run(
            frequency="weekly", day_of_week=0, time_of_day="08:00", after=now
        )
        assert nxt == datetime(2024, 6, 3, 8, 0, 0, tzinfo=timezone.utc)

    def test_compute_next_run_weekly_same_day_past_rolls_next_week(self):
        # 2024-06-03 is Monday at 10:00, target Monday at 08:00 → next Monday
        now = datetime(2024, 6, 3, 10, 0, 0, tzinfo=timezone.utc)
        nxt = notification_schedule_service.compute_next_run(
            frequency="weekly", day_of_week=0, time_of_day="08:00", after=now
        )
        assert nxt == datetime(2024, 6, 10, 8, 0, 0, tzinfo=timezone.utc)

    def test_get_due_schedules_returns_past(self, db: Session):
        org = _make_org(db)
        sched = notification_schedule_service.upsert_schedule(db, org.id, frequency="daily")
        # Force next_run_at to 1 hour ago
        sched.next_run_at = _utcnow() - timedelta(hours=1)
        db.commit()
        due = notification_schedule_service.get_due_schedules(db)
        assert any(s.id == sched.id for s in due)

    def test_get_due_schedules_skips_future(self, db: Session):
        org = _make_org(db)
        sched = notification_schedule_service.upsert_schedule(db, org.id, frequency="daily")
        sched.next_run_at = _utcnow() + timedelta(hours=1)
        db.commit()
        due = notification_schedule_service.get_due_schedules(db)
        assert not any(s.id == sched.id for s in due)

    def test_mark_run_updates_timestamps(self, db: Session):
        org = _make_org(db)
        sched = notification_schedule_service.upsert_schedule(db, org.id, frequency="daily")
        sched.next_run_at = _utcnow() - timedelta(hours=1)
        db.commit()

        notification_schedule_service.mark_run(db, sched)
        db.refresh(sched)
        assert sched.last_run_at is not None
        assert sched.next_run_at is not None

    def test_get_due_schedules_skips_disabled(self, db: Session):
        org = _make_org(db)
        sched = notification_schedule_service.upsert_schedule(
            db, org.id, frequency="daily", is_enabled=False
        )
        sched.next_run_at = _utcnow() - timedelta(hours=1)
        db.commit()
        due = notification_schedule_service.get_due_schedules(db)
        assert not any(s.id == sched.id for s in due)


# ─────────────────────────────────────────────────────────────────────────────
# notification_snooze_service
# ─────────────────────────────────────────────────────────────────────────────

class TestSnoozeService:
    def test_is_snoozed_org_wide(self, db: Session):
        org = _make_org(db)
        notification_snooze_service.create_snooze(
            db,
            org_id=org.id,
            entity_key="top_actions",
            snooze_until=_utcnow() + timedelta(hours=1),
            user_id=None,
        )
        assert notification_snooze_service.is_snoozed(db, org.id, "top_actions") is True

    def test_is_snoozed_user_specific_blocks_user(self, db: Session):
        org = _make_org(db)
        user_id = uuid4()
        notification_snooze_service.create_snooze(
            db,
            org_id=org.id,
            entity_key="critical_alert",
            snooze_until=_utcnow() + timedelta(hours=1),
            user_id=user_id,
        )
        assert notification_snooze_service.is_snoozed(db, org.id, "critical_alert", user_id=user_id) is True
        # A different user is not blocked
        assert notification_snooze_service.is_snoozed(db, org.id, "critical_alert", user_id=uuid4()) is False

    def test_is_snoozed_expired_returns_false(self, db: Session):
        org = _make_org(db)
        notification_snooze_service.create_snooze(
            db,
            org_id=org.id,
            entity_key="top_actions",
            snooze_until=_utcnow() - timedelta(seconds=1),  # already expired
        )
        assert notification_snooze_service.is_snoozed(db, org.id, "top_actions") is False

    def test_is_snoozed_different_entity_not_blocked(self, db: Session):
        org = _make_org(db)
        notification_snooze_service.create_snooze(
            db, org_id=org.id, entity_key="top_actions", snooze_until=_utcnow() + timedelta(hours=1)
        )
        assert notification_snooze_service.is_snoozed(db, org.id, "critical_alert") is False

    def test_delete_snooze(self, db: Session):
        org = _make_org(db)
        snooze = notification_snooze_service.create_snooze(
            db, org_id=org.id, entity_key="top_actions", snooze_until=_utcnow() + timedelta(hours=1)
        )
        assert notification_snooze_service.delete_snooze(db, snooze.id, org.id) is True
        assert notification_snooze_service.is_snoozed(db, org.id, "top_actions") is False

    def test_delete_snooze_wrong_org_returns_false(self, db: Session):
        org = _make_org(db)
        org2 = _make_org(db)
        snooze = notification_snooze_service.create_snooze(
            db, org_id=org.id, entity_key="top_actions", snooze_until=_utcnow() + timedelta(hours=1)
        )
        assert notification_snooze_service.delete_snooze(db, snooze.id, org2.id) is False

    def test_list_snoozes_excludes_expired(self, db: Session):
        org = _make_org(db)
        active = notification_snooze_service.create_snooze(
            db, org_id=org.id, entity_key="active_key", snooze_until=_utcnow() + timedelta(hours=1)
        )
        notification_snooze_service.create_snooze(
            db, org_id=org.id, entity_key="expired_key", snooze_until=_utcnow() - timedelta(seconds=1)
        )
        active_list = notification_snooze_service.list_snoozes(db, org.id)
        ids = [s.id for s in active_list]
        assert active.id in ids
        # expired one should not be present
        all_ids = [s.entity_key for s in active_list]
        assert "expired_key" not in all_ids

    def test_expire_old_snoozes(self, db: Session):
        org = _make_org(db)
        notification_snooze_service.create_snooze(
            db, org_id=org.id, entity_key="old1", snooze_until=_utcnow() - timedelta(hours=1)
        )
        notification_snooze_service.create_snooze(
            db, org_id=org.id, entity_key="old2", snooze_until=_utcnow() - timedelta(seconds=1)
        )
        notification_snooze_service.create_snooze(
            db, org_id=org.id, entity_key="future", snooze_until=_utcnow() + timedelta(hours=1)
        )
        deleted = notification_snooze_service.expire_old_snoozes(db)
        assert deleted == 2
        remaining = notification_snooze_service.list_snoozes(db, org.id)
        assert len(remaining) == 1
        assert remaining[0].entity_key == "future"


# ─────────────────────────────────────────────────────────────────────────────
# notification_retry_service
# ─────────────────────────────────────────────────────────────────────────────

class TestRetryService:
    def test_backoff_seconds_values(self):
        assert backoff_seconds(0) == _BACKOFF_SECONDS[0]
        assert backoff_seconds(1) == _BACKOFF_SECONDS[1]
        assert backoff_seconds(2) == _BACKOFF_SECONDS[2]
        # Beyond max returns last
        assert backoff_seconds(99) == _BACKOFF_SECONDS[-1]

    def test_schedule_retry_sets_next_retry_at(self, db: Session):
        org = _make_org(db)
        entry = _log(db, org.id, status="failed", retry_count=0)
        result = schedule_retry(db, entry)
        assert result is True
        db.refresh(entry)
        assert entry.next_retry_at is not None

    def test_schedule_retry_at_max_marks_permanently_failed(self, db: Session):
        org = _make_org(db)
        entry = _log(db, org.id, status="failed", retry_count=MAX_RETRIES)
        result = schedule_retry(db, entry)
        assert result is False
        db.refresh(entry)
        assert entry.status == "permanently_failed"
        assert entry.next_retry_at is None

    def test_get_retryable_logs_returns_due(self, db: Session):
        org = _make_org(db)
        entry = _log(db, org.id, status="failed", retry_count=0)
        entry.next_retry_at = _utcnow() - timedelta(seconds=1)
        db.commit()
        due = get_retryable_logs(db)
        assert any(r.id == entry.id for r in due)

    def test_get_retryable_logs_skips_future(self, db: Session):
        org = _make_org(db)
        entry = _log(db, org.id, status="failed", retry_count=0)
        entry.next_retry_at = _utcnow() + timedelta(hours=1)
        db.commit()
        due = get_retryable_logs(db)
        assert not any(r.id == entry.id for r in due)

    def test_get_retryable_logs_skips_permanently_failed(self, db: Session):
        org = _make_org(db)
        entry = _log(db, org.id, status="permanently_failed", retry_count=3)
        entry.next_retry_at = _utcnow() - timedelta(seconds=1)
        db.commit()
        due = get_retryable_logs(db)
        assert not any(r.id == entry.id for r in due)

    def test_delivery_log_retry_columns(self, db: Session):
        """NotificationDeliveryLog has retry_count, next_retry_at, provider_response."""
        org = _make_org(db)
        log = NotificationDeliveryLog(
            organization_id=org.id,
            provider="slack",
            event_type="top_actions",
            target_type="org",
            target_key=str(org.id),
            route_kind="org_channel",
            dedupe_key=f"test-{uuid4().hex}",
            status="failed",
            retry_count=1,
            next_retry_at=_utcnow() + timedelta(minutes=2),
            provider_response={"foo": "bar"},
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        assert log.retry_count == 1
        assert log.next_retry_at is not None
        assert log.provider_response == {"foo": "bar"}


# ─────────────────────────────────────────────────────────────────────────────
# API: notification-policy
# ─────────────────────────────────────────────────────────────────────────────

def _admin_headers(org_id):
    return {
        "X-User": "test-admin@test.local",
        "X-Role": "root_admin",
        "X-Current-Organization-Id": str(org_id),
    }


class TestNotificationPolicyAPI:
    def test_get_policy_creates_defaults(self, client: TestClient, dev_org_scope: dict):
        org = dev_org_scope["org"]
        resp = client.get(f"/api/v1/orgs/{org.id}/notification-policy", headers=_admin_headers(org.id))
        assert resp.status_code == 200
        data = resp.json()
        assert data["min_priority"] == "low"
        assert data["throttle_window_minutes"] == 60
        assert data["digest_mode"] == "instant"

    def test_update_policy(self, client: TestClient, dev_org_scope: dict):
        org = dev_org_scope["org"]
        resp = client.put(
            f"/api/v1/orgs/{org.id}/notification-policy",
            json={"min_priority": "high", "throttle_window_minutes": 120},
            headers=_admin_headers(org.id),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["min_priority"] == "high"
        assert data["throttle_window_minutes"] == 120

    def test_update_policy_invalid_priority(self, client: TestClient, dev_org_scope: dict):
        org = dev_org_scope["org"]
        resp = client.put(
            f"/api/v1/orgs/{org.id}/notification-policy",
            json={"min_priority": "critical"},
            headers=_admin_headers(org.id),
        )
        assert resp.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# API: notification-schedule
# ─────────────────────────────────────────────────────────────────────────────

class TestNotificationScheduleAPI:
    def test_get_schedule_no_existing_returns_null(self, client: TestClient, dev_org_scope: dict):
        org = dev_org_scope["org"]
        resp = client.get(f"/api/v1/orgs/{org.id}/notification-schedule", headers=_admin_headers(org.id))
        assert resp.status_code == 200
        data = resp.json()
        assert data is None or isinstance(data, dict)

    def test_upsert_schedule_creates(self, client: TestClient, dev_org_scope: dict):
        org = dev_org_scope["org"]
        resp = client.put(
            f"/api/v1/orgs/{org.id}/notification-schedule",
            json={"frequency": "daily", "time_of_day": "09:00", "timezone": "UTC", "is_enabled": True},
            headers=_admin_headers(org.id),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["frequency"] == "daily"
        assert data["time_of_day"] == "09:00"

    def test_upsert_schedule_weekly(self, client: TestClient, dev_org_scope: dict):
        org = dev_org_scope["org"]
        resp = client.put(
            f"/api/v1/orgs/{org.id}/notification-schedule",
            json={
                "frequency": "weekly",
                "day_of_week": 1,
                "time_of_day": "08:00",
                "timezone": "UTC",
                "is_enabled": True,
            },
            headers=_admin_headers(org.id),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["frequency"] == "weekly"
        assert data["day_of_week"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# API: notification-snooze
# ─────────────────────────────────────────────────────────────────────────────

class TestNotificationSnoozeAPI:
    def _future_dt(self) -> str:
        return (_utcnow() + timedelta(hours=2)).isoformat()

    def test_list_snoozes_empty(self, client: TestClient, dev_org_scope: dict):
        org = dev_org_scope["org"]
        resp = client.get(f"/api/v1/orgs/{org.id}/notification-snoozes", headers=_admin_headers(org.id))
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_snooze(self, client: TestClient, dev_org_scope: dict):
        org = dev_org_scope["org"]
        resp = client.post(
            f"/api/v1/orgs/{org.id}/notification-snooze",
            json={"entity_key": "top_actions", "snooze_until": self._future_dt(), "reason": "on vacation"},
            headers=_admin_headers(org.id),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["entity_key"] == "top_actions"
        assert "id" in data

    def test_delete_snooze(self, client: TestClient, dev_org_scope: dict):
        org = dev_org_scope["org"]
        h = _admin_headers(org.id)
        # Create first
        create_resp = client.post(
            f"/api/v1/orgs/{org.id}/notification-snooze",
            json={"entity_key": "critical_alert", "snooze_until": self._future_dt()},
            headers=h,
        )
        snooze_id = create_resp.json()["id"]
        # Delete
        del_resp = client.delete(
            f"/api/v1/orgs/{org.id}/notification-snooze/{snooze_id}",
            headers=h,
        )
        assert del_resp.status_code == 204
        # Confirm gone
        list_resp = client.get(f"/api/v1/orgs/{org.id}/notification-snoozes", headers=h)
        ids = [s["id"] for s in list_resp.json()]
        assert str(snooze_id) not in ids

    def test_list_snoozes_after_create(self, client: TestClient, dev_org_scope: dict):
        org = dev_org_scope["org"]
        h = _admin_headers(org.id)
        client.post(
            f"/api/v1/orgs/{org.id}/notification-snooze",
            json={"entity_key": "top_actions", "snooze_until": self._future_dt()},
            headers=h,
        )
        resp = client.get(f"/api/v1/orgs/{org.id}/notification-snoozes", headers=h)
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["entity_key"] == "top_actions"


# ─────────────────────────────────────────────────────────────────────────────
# API: notification-health
# ─────────────────────────────────────────────────────────────────────────────

class TestNotificationHealthAPI:
    def test_get_health_empty(self, client: TestClient, dev_org_scope: dict):
        org = dev_org_scope["org"]
        resp = client.get(f"/api/v1/orgs/{org.id}/notification-health", headers=_admin_headers(org.id))
        assert resp.status_code == 200
        data = resp.json()
        assert "delivery_count_24h" in data
        assert "failure_count_24h" in data
        assert "pending_retries" in data
        assert "integrations" in data
        assert data["delivery_count_24h"] == 0
        assert data["failure_count_24h"] == 0

    def test_get_health_counts_recent_logs(self, client: TestClient, dev_org_scope: dict, db: Session):
        org = dev_org_scope["org"]

        # Add a delivered log within 24h
        delivered = NotificationDeliveryLog(
            organization_id=org.id,
            provider="slack",
            event_type="top_actions",
            target_type="org",
            target_key=str(org.id),
            route_kind="org_channel",
            dedupe_key=f"test-{uuid4().hex}",
            status="delivered",
        )
        # Add a failed log within 24h
        failed = NotificationDeliveryLog(
            organization_id=org.id,
            provider="slack",
            event_type="top_actions",
            target_type="org",
            target_key=str(org.id),
            route_kind="org_channel",
            dedupe_key=f"test-{uuid4().hex}",
            status="failed",
        )
        db.add_all([delivered, failed])
        db.commit()

        resp = client.get(f"/api/v1/orgs/{org.id}/notification-health", headers=_admin_headers(org.id))
        assert resp.status_code == 200
        data = resp.json()
        assert data["delivery_count_24h"] == 1
        assert data["failure_count_24h"] == 1
