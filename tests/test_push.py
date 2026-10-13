import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import http_ece
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from py_vapid import Vapid

from vitadrive import push
from vitadrive.adapters.simulator import VehicleSimulator
from vitadrive.api import create_app
from vitadrive.models import Vehicle

CAR = {"vin": "VDPUSH0000000001", "make": "Toyota", "model": "Corolla", "year": 2023, "powertrain": "ice"}
BASE = f"/api/vehicles/{CAR['vin']}"
ALICE, BOB = {"Authorization": "alice"}, {"Authorization": "bob"}


def sub(name):
    return {"endpoint": f"https://push.example/{name}", "keys": {"p256dh": "B" * 40, "auth": "A" * 16}}


class HeaderAuth:
    enabled, publishable_key, frontend_api = True, None, None

    def user_id(self, authorization):
        return authorization or "anon"


@pytest.fixture
def keys(monkeypatch):
    public, private = push.generate_keys()
    monkeypatch.setenv("VAPID_PUBLIC_KEY", public)
    monkeypatch.setenv("VAPID_PRIVATE_KEY", private)
    return public, private


@pytest.fixture
def sent(monkeypatch):
    """Record deliveries instead of calling real push services; status per endpoint can be overridden."""
    log, status = [], {}

    def deliver(s, data, ttl):
        log.append((s["endpoint"], json.loads(data)))
        return status.get(s["endpoint"], 201)

    monkeypatch.setattr(push, "_deliver", deliver)
    return log, status


@pytest.fixture
def client(tmp_path):
    c = TestClient(create_app(str(tmp_path / "push.db"), auth=HeaderAuth()))
    c.put(BASE, json=CAR, headers=ALICE).raise_for_status()
    return c


def post_reading(client, **changes):
    reading = next(VehicleSimulator(Vehicle(**CAR), seed=1).history(days=1)).model_copy(update=changes)
    key = client.post(f"{BASE}/api-key", headers=ALICE).json()["api_key"]
    r = client.post(f"{BASE}/readings", content=reading.model_dump_json(),
                    headers={"X-API-Key": key, "Content-Type": "application/json"})
    r.raise_for_status()
    return r.json()


def test_config_exposes_public_key_only_when_configured(client, monkeypatch):
    monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)
    assert client.get("/api/config").json()["push_public_key"] is None
    assert client.post("/api/push/subscriptions", json=sub("a"), headers=ALICE).status_code == 503
    public, private = push.generate_keys()
    monkeypatch.setenv("VAPID_PUBLIC_KEY", f' "{public}" ')
    monkeypatch.setenv("VAPID_PRIVATE_KEY", private)
    assert client.get("/api/config").json()["push_public_key"] == public


def test_generated_keys_are_a_valid_vapid_pair():
    public, private = push.generate_keys()
    v = Vapid.from_string(private)
    raw = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    assert base64.urlsafe_b64encode(raw).rstrip(b"=").decode() == public and len(raw) == 65


def test_subscription_must_be_https(client, keys):
    bad = {**sub("a"), "endpoint": "http://169.254.169.254/latest"}
    assert client.post("/api/push/subscriptions", json=bad, headers=ALICE).status_code == 422


def test_critical_reading_notifies_owner_once(client, keys, sent):
    log, _ = sent
    client.post("/api/push/subscriptions", json=sub("alice-phone"), headers=ALICE).raise_for_status()
    client.post("/api/push/subscriptions", json=sub("bob-phone"), headers=BOB).raise_for_status()
    alerts = post_reading(client, tire_pressure_fl_kpa=150)
    assert any(a["severity"] == "critical" for a in alerts)
    assert [e for e, _ in log] == ["https://push.example/alice-phone"]
    msg = log[0][1]
    assert msg["title"] == "2023 Toyota Corolla: 1 critical issue" and "front-left" in msg["body"].lower()
    assert msg["url"] == f"/?vin={CAR['vin']}"
    post_reading(client, tire_pressure_fl_kpa=150)  # still open: no repeat notification
    assert len(log) == 1


def test_warning_does_not_notify(client, keys, sent):
    log, _ = sent
    client.post("/api/push/subscriptions", json=sub("alice-phone"), headers=ALICE)
    alerts = post_reading(client, tire_pressure_fl_kpa=200)
    assert alerts and all(a["severity"] != "critical" for a in alerts) and log == []


def test_expired_subscription_is_removed(client, keys, sent):
    log, status = sent
    client.post("/api/push/subscriptions", json=sub("old"), headers=ALICE)
    client.post("/api/push/subscriptions", json=sub("new"), headers=ALICE)
    status["https://push.example/old"] = 410
    assert client.post("/api/push/test", headers=ALICE).json() == {"sent": 1}
    assert client.post("/api/push/test", headers=ALICE).json() == {"sent": 1}
    assert [e for e, _ in log].count("https://push.example/old") == 1


def test_delivery_failure_does_not_break_ingest(client, keys, monkeypatch):
    client.post("/api/push/subscriptions", json=sub("alice-phone"), headers=ALICE)
    monkeypatch.setattr(push, "send_to_user", lambda *a, **k: 1 / 0)
    assert post_reading(client, tire_pressure_fl_kpa=150)


def test_unsubscribe_only_removes_own_device(client, keys, sent):
    log, _ = sent
    client.post("/api/push/subscriptions", json=sub("alice-phone"), headers=ALICE)
    q = {"endpoint": "https://push.example/alice-phone"}
    assert client.delete("/api/push/subscriptions", params=q, headers=BOB).status_code == 204
    assert client.post("/api/push/test", headers=ALICE).json() == {"sent": 1}
    client.delete("/api/push/subscriptions", params=q, headers=ALICE)
    assert client.post("/api/push/test", headers=ALICE).json() == {"sent": 0}


def test_device_moves_to_the_account_that_subscribed_last(client, keys, sent):
    client.post("/api/push/subscriptions", json=sub("shared"), headers=ALICE)
    client.post("/api/push/subscriptions", json=sub("shared"), headers=BOB)
    assert client.post("/api/push/test", headers=ALICE).json() == {"sent": 0}
    assert client.post("/api/push/test", headers=BOB).json() == {"sent": 1}


def test_service_worker_is_served_from_root(client):
    r = client.get("/sw.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"] and "showNotification" in r.text


def test_real_delivery_is_encrypted_and_vapid_signed(keys):
    """End to end through pywebpush: a fake push service receives a payload only the browser key can decrypt."""
    browser = ec.generate_private_key(ec.SECP256R1())
    auth = b"0123456789abcdef"
    got = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            got["auth"] = self.headers["Authorization"]
            got["body"] = self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(201)
            self.end_headers()

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()
    pub = browser.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    s = {"endpoint": f"http://127.0.0.1:{server.server_port}/send",
         "keys": {"p256dh": push._b64(pub), "auth": push._b64(auth)}}
    assert push._deliver(s, json.dumps({"title": "hi"}), 60) == 201
    server.server_close()
    assert got["auth"].startswith("vapid t=")
    plain = http_ece.decrypt(got["body"], private_key=browser, auth_secret=auth, version="aes128gcm")
    assert json.loads(plain) == {"title": "hi"}
