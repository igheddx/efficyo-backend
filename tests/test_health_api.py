from pathlib import Path

from fastapi.testclient import TestClient

from app import main as main_module


class _HealthySession:
    def execute(self, _query):
        return 1

    def close(self):
        return None


class _FailingSession:
    def execute(self, _query):
        raise RuntimeError("db unavailable")

    def close(self):
        return None


def test_health_check_ok_with_fresh_worker_heartbeat(client: TestClient, monkeypatch, tmp_path: Path):
    heartbeat_path = tmp_path / "worker-heartbeat.json"
    heartbeat_path.write_text('{"state":"idle"}', encoding="utf-8")

    monkeypatch.setattr(main_module, "WORKER_HEARTBEAT_PATH", heartbeat_path)
    monkeypatch.setattr(main_module, "SessionLocal", lambda: _HealthySession())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database"] == "ok"
    assert response.json()["worker"] == "ok"
    assert response.json()["worker_last_seen"] is not None


def test_health_check_degraded_without_worker_heartbeat(client: TestClient, monkeypatch, tmp_path: Path):
    monkeypatch.setattr(main_module, "WORKER_HEARTBEAT_PATH", tmp_path / "missing-heartbeat.json")
    monkeypatch.setattr(main_module, "SessionLocal", lambda: _HealthySession())

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "database": "ok",
        "worker": "stale",
        "worker_last_seen": None,
    }


def test_health_check_degraded_when_database_unavailable(client: TestClient, monkeypatch, tmp_path: Path):
    heartbeat_path = tmp_path / "worker-heartbeat.json"
    heartbeat_path.write_text('{"state":"idle"}', encoding="utf-8")

    monkeypatch.setattr(main_module, "WORKER_HEARTBEAT_PATH", heartbeat_path)
    monkeypatch.setattr(main_module, "SessionLocal", lambda: _FailingSession())

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["database"] == "error"
    assert response.json()["worker"] == "ok"