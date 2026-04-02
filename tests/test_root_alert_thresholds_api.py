from fastapi.testclient import TestClient


def test_root_get_alert_thresholds_default(client: TestClient):
    response = client.get(
        "/api/v1/root/alerts/thresholds",
        headers={"X-User": "root", "X-Role": "root_admin"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["worker_stale_seconds"] == 180
    assert body["backup_max_age_hours"] == 26
    assert body["disk_usage_warn_percent"] == 80
    assert body["disk_usage_critical_percent"] == 90


def test_root_patch_alert_thresholds(client: TestClient):
    response = client.patch(
        "/api/v1/root/alerts/thresholds",
        headers={"X-User": "root", "X-Role": "root_admin"},
        json={"backup_max_age_hours": 30, "disk_usage_warn_percent": 82, "disk_usage_critical_percent": 92},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["backup_max_age_hours"] == 30
    assert body["disk_usage_warn_percent"] == 82
    assert body["disk_usage_critical_percent"] == 92

    check = client.get(
        "/api/v1/root/alerts/thresholds",
        headers={"X-User": "root", "X-Role": "root_admin"},
    )
    assert check.status_code == 200
    assert check.json()["backup_max_age_hours"] == 30


def test_root_patch_alert_thresholds_rejects_invalid_bounds(client: TestClient):
    response = client.patch(
        "/api/v1/root/alerts/thresholds",
        headers={"X-User": "root", "X-Role": "root_admin"},
        json={"disk_usage_warn_percent": 90, "disk_usage_critical_percent": 89},
    )
    assert response.status_code == 400


def test_root_alert_thresholds_forbidden_for_non_root(client: TestClient):
    response = client.get(
        "/api/v1/root/alerts/thresholds",
        headers={"X-User": "viewer", "X-Role": "viewer"},
    )
    assert response.status_code == 403