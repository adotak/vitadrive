from __future__ import annotations

from fastapi.testclient import TestClient

import vitadrive.api as api
from vitadrive import __version__


def test_vercel_without_database_keeps_static_and_config_available(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("VITADRIVE_DB", raising=False)

    app = api.create_app()
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/config").status_code == 200
        assert client.get("/static/icon-192.png").status_code == 200

        health = client.get("/api/health")
        assert health.status_code == 503
        assert health.json() == {
            "ok": False,
            "version": __version__,
            "database": "unavailable",
            "reason": "missing_database_url",
            "hint": "Set DATABASE_URL in Vercel → Settings → Environment Variables, then redeploy.",
        }
        assert health.headers["Cache-Control"] == "no-store"
        vehicles = client.get("/api/vehicles")
        assert vehicles.status_code == 503
        assert vehicles.json() == {
            "detail": "Database unavailable",
            "error": {
                "status": 503,
                "code": "unavailable",
                "message": "Database unavailable. Try again in a minute.",
                "request_id": vehicles.headers["X-Request-ID"],
            },
        }
        assert vehicles.headers.get("X-Request-ID")
        assert vehicles.headers["X-Content-Type-Options"] == "nosniff"


def test_unreachable_database_reports_safe_connection_failure():
    app = api.create_app(db="postgresql://u:supersecret@127.0.0.1:1/x")
    with TestClient(app) as client:
        responses = [client.get("/api/health"), client.get("/api/vehicles")]

    assert responses[0].status_code == 503
    assert responses[0].json()["reason"] == "connection_failed"
    assert all(response.status_code == 503 for response in responses)
    assert all("supersecret" not in response.text for response in responses)
    assert all("127.0.0.1" not in response.text for response in responses)


def test_startup_recovers_when_database_becomes_available(monkeypatch, tmp_path):
    real_store = api.Store
    calls = 0
    clock = [0.0]

    def fail_once(target):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("first connection attempt failed")
        return real_store(target)

    monkeypatch.setattr(api, "Store", fail_once)
    monkeypatch.setattr(api.time_module, "monotonic", lambda: clock[0])
    app = api.create_app(str(tmp_path / "recovered.db"))
    with TestClient(app) as client:
        clock[0] = 16.0
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["ok"] is True
        vehicles = client.get("/api/vehicles")
        assert vehicles.status_code == 200
        assert vehicles.json() == []
    app.state.vitadrive.store.close()
    assert calls == 2


def test_failed_startup_retries_are_throttled(monkeypatch):
    calls = 0
    monkeypatch.setattr(api.time_module, "monotonic", lambda: 0.0)

    def fail(_target):
        nonlocal calls
        calls += 1
        raise RuntimeError("not available")

    monkeypatch.setattr(api, "Store", fail)
    app = api.create_app(db="sqlite:///unused.db")
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 503
        assert client.get("/api/vehicles").status_code == 503
    assert calls == 1
