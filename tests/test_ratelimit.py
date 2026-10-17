import os
import uuid

import pytest
from fastapi.testclient import TestClient
from langchain_core.runnables import RunnableLambda
from sqlalchemy import delete, insert, select

from vitadrive import nearby
from vitadrive.api import create_app
from vitadrive.models import SensorReading
from vitadrive.ratelimit import Limit, RateLimited, RateLimiter
from vitadrive.storage import Store, rate_limits_t

PG_URL = os.environ.get("VITADRIVE_TEST_POSTGRES")
CAR = {"vin": "VDRATELIMIT000001", "make": "Toyota", "model": "Corolla", "year": 2023, "powertrain": "ice"}
BASE = f"/api/vehicles/{CAR['vin']}"
ALICE = {"Authorization": "alice"}


class HeaderAuth:
    enabled, publishable_key, frontend_api = True, None, None

    def user_id(self, authorization):
        return authorization or "anon"


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "limits.db")
    try:
        yield instance
    finally:
        instance.close()


def _client(tmp_path, name, *, limits=None, llm=None):
    return TestClient(create_app(str(tmp_path / f"{name}.db"), auth=HeaderAuth(), llm=llm, limits=limits))


def _client_with_vehicle(tmp_path, name, *, limits=None, llm=None):
    client = _client(tmp_path, name, limits=limits, llm=llm)
    response = client.put(BASE, json=CAR, headers=ALICE)
    assert response.status_code == 200
    return client


def _ask(client, question="hello"):
    return client.post(f"{BASE}/ask", json={"question": question}, headers=ALICE)


def _assert_limited(response):
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1
    assert response.json()["detail"]


def test_limiter_allows_limit_then_reports_exact_retry_after(store):
    now = [125.5]
    limiter = RateLimiter(store, clock=lambda: now[0])
    limit = Limit("unit", max_hits=2, window_s=60)

    limiter.hit(limit, "alice")
    limiter.hit(limit, "alice")
    with pytest.raises(RateLimited) as error:
        limiter.hit(limit, "alice")
    assert error.value.retry_after == 55
    assert error.value.message == str(error.value)


def test_limiter_resets_in_a_new_window(store):
    now = [59.0]
    limiter = RateLimiter(store, clock=lambda: now[0])
    limit = Limit("window", max_hits=1, window_s=60)

    limiter.hit(limit, "alice")
    with pytest.raises(RateLimited):
        limiter.hit(limit, "alice")
    now[0] = 60
    limiter.hit(limit, "alice")


def test_limiter_tracks_subjects_independently(store):
    limiter = RateLimiter(store, clock=lambda: 120)
    limit = Limit("independent", max_hits=1, window_s=60)

    limiter.hit(limit, "alice")
    with pytest.raises(RateLimited):
        limiter.hit(limit, "alice")
    limiter.hit(limit, "bob")


def test_limiter_deletes_old_windows_for_the_same_key(store):
    now = 125.0
    limiter = RateLimiter(store, clock=lambda: now)
    limit = Limit("cleanup", max_hits=3, window_s=60)
    key = "cleanup:alice"
    with store.transaction() as connection:
        connection.execute(insert(rate_limits_t).values(key=key, window_start=60, count=99))

    limiter.hit(limit, "alice")

    with store.transaction() as connection:
        rows = connection.execute(
            select(rate_limits_t.c.window_start, rate_limits_t.c.count)
            .where(rate_limits_t.c.key == key)
        ).all()
    assert rows == [(120, 1)]


def test_two_limiter_instances_share_sqlite_counts(tmp_path):
    path = tmp_path / "shared.db"
    first, second = Store(path), Store(path)
    limit = Limit("shared", max_hits=1, window_s=60)
    try:
        RateLimiter(first, clock=lambda: 120).hit(limit, "alice")
        with pytest.raises(RateLimited):
            RateLimiter(second, clock=lambda: 120).hit(limit, "alice")
    finally:
        first.close()
        second.close()


@pytest.mark.skipif(not PG_URL, reason="set VITADRIVE_TEST_POSTGRES to run against Postgres")
def test_two_limiter_instances_share_postgres_counts():
    first, second = Store(PG_URL), Store(PG_URL)
    limit = Limit("shared-pg", max_hits=1, window_s=60)
    subject = uuid.uuid4().hex
    key = f"{limit.name}:{subject}"
    try:
        RateLimiter(first, clock=lambda: 120).hit(limit, subject)
        with pytest.raises(RateLimited):
            RateLimiter(second, clock=lambda: 120).hit(limit, subject)
    finally:
        with first.transaction() as connection:
            connection.execute(delete(rate_limits_t).where(rate_limits_t.c.key == key))
        first.close()
        second.close()


def test_demo_limit_returns_429_with_retry_after(tmp_path):
    client = _client(
        tmp_path,
        "demo",
        limits={"demo": Limit("demo", max_hits=1, window_s=3600)},
    )
    assert client.post("/api/demo", headers=ALICE).status_code == 201
    _assert_limited(client.post("/api/demo", headers=ALICE))


def test_ask_rate_limit_and_daily_quota_message(tmp_path):
    llm = RunnableLambda(lambda _: "answer")
    minute_client = _client_with_vehicle(
        tmp_path,
        "ask-minute",
        limits={"ask": Limit("ask", max_hits=1, window_s=60)},
        llm=llm,
    )
    assert _ask(minute_client).status_code == 200
    _assert_limited(_ask(minute_client))

    daily_client = _client_with_vehicle(
        tmp_path,
        "ask-daily",
        limits={
            "ask": Limit("ask", max_hits=10, window_s=60),
            "ask_daily": Limit("ask_daily", max_hits=1, window_s=86400),
        },
        llm=llm,
    )
    assert _ask(daily_client).status_code == 200
    daily_response = _ask(daily_client)
    _assert_limited(daily_response)
    assert "resets at midnight UTC" in daily_response.json()["detail"]


def test_disabled_ask_does_not_consume_daily_quota(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = _client_with_vehicle(
        tmp_path,
        "ask-disabled",
        limits={"ask_daily": Limit("ask_daily", max_hits=1, window_s=86400)},
    )
    assert _ask(client).status_code == 503
    with client.app.state.vitadrive.store.transaction() as connection:
        count = connection.execute(
            select(rate_limits_t.c.count).where(rate_limits_t.c.key == "ask_daily:alice")
        ).scalar_one_or_none()
    assert count is None


def test_readings_rate_limit_runs_after_api_key_authentication(tmp_path):
    client = _client_with_vehicle(
        tmp_path,
        "readings",
        limits={"readings": Limit("readings", max_hits=1, window_s=60)},
    )
    key = client.post(f"{BASE}/api-key", headers=ALICE).json()["api_key"]
    reading = SensorReading(vin=CAR["vin"], odometer_km=100).model_dump(mode="json")
    assert client.post(f"{BASE}/readings", json=reading).status_code == 404
    valid = client.post(f"{BASE}/readings", json=reading, headers={"X-API-Key": key})
    assert valid.status_code == 201
    _assert_limited(client.post(f"{BASE}/readings", json=reading, headers={"X-API-Key": key}))


def test_nearby_only_limits_searches_with_a_location(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(nearby, "geocode", lambda query: (51.5, -0.1))
    monkeypatch.setattr(nearby, "search", lambda lat, lon, kind: calls.append((lat, lon, kind)) or (10, []))
    client = _client_with_vehicle(
        tmp_path,
        "nearby",
        limits={"nearby": Limit("nearby", max_hits=1, window_s=60)},
    )

    assert client.get(f"{BASE}/nearby", headers=ALICE).status_code == 200
    assert client.get(f"{BASE}/nearby", params={"q": "London"}, headers=ALICE).status_code == 200
    _assert_limited(client.get(f"{BASE}/nearby", params={"q": "London"}, headers=ALICE))
    assert calls == [(51.5, -0.1, "mechanic")]


def test_api_key_limit_returns_429(tmp_path):
    client = _client_with_vehicle(
        tmp_path,
        "api-key",
        limits={"api_key": Limit("api_key", max_hits=1, window_s=3600)},
    )
    assert client.post(f"{BASE}/api-key", headers=ALICE).status_code == 200
    _assert_limited(client.post(f"{BASE}/api-key", headers=ALICE))


def test_reading_and_alert_limits_are_bounded(tmp_path):
    client = _client_with_vehicle(tmp_path, "query-limits")
    for endpoint in ("readings", "alerts"):
        path = f"{BASE}/{endpoint}"
        assert client.get(path, params={"limit": 1001}, headers=ALICE).status_code == 422
        assert client.get(path, params={"limit": 1000}, headers=ALICE).status_code == 200


def test_ask_rejects_questions_longer_than_500_characters(tmp_path):
    client = _client_with_vehicle(
        tmp_path,
        "ask-length",
        llm=RunnableLambda(lambda _: "answer"),
    )
    assert _ask(client, "x" * 501).status_code == 422


def test_rate_limits_can_be_disabled_for_local_development(tmp_path, monkeypatch):
    monkeypatch.setenv("VITADRIVE_RATE_LIMITS", "0")
    client = _client_with_vehicle(
        tmp_path,
        "disabled",
        limits={"api_key": Limit("api_key", max_hits=1, window_s=3600)},
    )
    assert client.post(f"{BASE}/api-key", headers=ALICE).status_code == 200
    assert client.post(f"{BASE}/api-key", headers=ALICE).status_code == 200
