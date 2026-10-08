import os
import time
import uuid
from datetime import date

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from vitadrive.api import create_app
from vitadrive.auth import AuthError, ClerkAuth, frontend_api_from_publishable_key
from vitadrive.models import Powertrain, SensorReading, ServiceRecord, Vehicle
from vitadrive.storage import Store, database_url

PG_URL = os.environ.get("VITADRIVE_TEST_POSTGRES")
PK = "pk_test_Y2xlcmsuZXhhbXBsZS5jb20k"  # base64("clerk.example.com$")


def vehicle(vin):
    return Vehicle(vin=vin, make="Ford", model="Focus", year=2022, powertrain=Powertrain.ICE, tank_capacity_l=50)


class FakeKey:
    def __init__(self, key):
        self.key = key


class FakeJWKS:
    def __init__(self, public_key):
        self.public_key = public_key

    def get_signing_key_from_jwt(self, _token):
        return FakeKey(self.public_key)


@pytest.fixture
def clerk():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    auth = ClerkAuth(publishable_key=PK, authorized_parties=["https://vitadrive.test"])
    auth._jwks = FakeJWKS(private.public_key())

    def token(sub, azp="https://vitadrive.test", exp_in=60):
        return "Bearer " + jwt.encode({"sub": sub, "azp": azp, "exp": int(time.time()) + exp_in}, private,
                                      algorithm="RS256")
    return auth, token


def test_frontend_api_decoding():
    assert frontend_api_from_publishable_key(PK) == "clerk.example.com"


def test_database_url_normalisation():
    assert database_url("postgres://u:p@h:6543/db") == "postgresql+psycopg://u:p@h:6543/db"
    assert database_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert database_url("data/vd.db") == "sqlite:///data/vd.db"


def test_clerk_token_verification(clerk):
    auth, token = clerk
    assert auth.user_id(token("user_1")) == "user_1"
    for bad in (None, "Bearer junk", token("user_1", exp_in=-120), token("user_1", azp="https://evil.test")):
        with pytest.raises(AuthError):
            auth.user_id(bad)


def test_auth_disabled_uses_local_owner():
    assert ClerkAuth().user_id(None) == "local"


def test_api_isolation_between_users_and_api_keys(tmp_path, clerk):
    auth, token = clerk
    client = TestClient(create_app(str(tmp_path / "a.db"), auth=auth))
    alice, bob = {"Authorization": token("user_alice")}, {"Authorization": token("user_bob")}
    car = vehicle("ALICECAR000000001").model_dump(mode="json")

    assert client.get("/api/vehicles").status_code == 401
    assert client.put(f"/api/vehicles/{car['vin']}", json=car, headers=alice).status_code == 200
    assert client.put(f"/api/vehicles/{car['vin']}", json=car, headers=bob).status_code == 409
    assert client.get("/api/vehicles", headers=bob).json() == []
    assert client.get(f"/api/vehicles/{car['vin']}/report", headers=bob).status_code == 404

    key = client.post(f"/api/vehicles/{car['vin']}/api-key", headers=alice).json()["api_key"]
    reading = {"vin": car["vin"], "odometer_km": 1000, "fuel_level_pct": 5}
    assert client.post(f"/api/vehicles/{car['vin']}/readings", json=reading).status_code == 401
    assert client.post(f"/api/vehicles/{car['vin']}/readings", json=reading,
                       headers={"X-API-Key": "vd_wrong"}).status_code == 401
    r = client.post(f"/api/vehicles/{car['vin']}/readings", json=reading, headers={"X-API-Key": key})
    assert r.status_code == 201
    alert_id = r.json()[0]["id"]
    assert client.post(f"/api/alerts/{alert_id}/ack", headers=bob).status_code == 404
    assert client.post(f"/api/alerts/{alert_id}/ack", headers=alice).status_code == 204

    demo = client.post("/api/demo", headers=bob).json()
    assert len(demo) == 2
    assert {v["vin"] for v in client.get("/api/vehicles", headers=bob).json()} == {v["vin"] for v in demo}
    assert client.get("/api/config").json() == {"auth_enabled": True, "clerk_publishable_key": PK,
                                                "clerk_frontend_api": "clerk.example.com",
                                                "assistant_enabled": False}
    assert client.delete(f"/api/vehicles/{car['vin']}", headers=alice).status_code == 204
    assert client.get("/api/vehicles", headers=alice).json() == []


@pytest.mark.skipif(not PG_URL, reason="set VITADRIVE_TEST_POSTGRES to run against Postgres")
def test_postgres_store_roundtrip():
    store = Store(PG_URL)
    vin = ("PG" + uuid.uuid4().hex.upper())[:17]
    try:
        store.upsert_vehicle(vehicle(vin), owner_id="user_pg")
        store.add_reading(SensorReading(vin=vin, odometer_km=100, fuel_level_pct=50))
        rec = store.add_service(ServiceRecord(vin=vin, item="oil_change", performed_on=date(2026, 1, 1),
                                              odometer_km=90))
        key = store.rotate_api_key(vin)
        assert store.vin_for_api_key(key) == vin
        assert store.latest_reading(vin).fuel_level_pct == 50
        assert store.services(vin)[0].id == rec.id
        assert vin in {v.vin for v in store.list_vehicles("user_pg")}
    finally:
        store.delete_vehicle(vin)
        store.close()


@pytest.mark.skipif(not PG_URL, reason="set VITADRIVE_TEST_POSTGRES to run against Postgres")
def test_postgres_api_flow():
    client = TestClient(create_app(PG_URL, auth=ClerkAuth()))
    vins = [v["vin"] for v in client.post("/api/demo").json()]
    try:
        report = client.get(f"/api/vehicles/{vins[0]}/report").json()
        assert report["maintenance"] and report["latest_reading"]
    finally:
        for vin in vins:
            client.delete(f"/api/vehicles/{vin}")


def test_authorized_parties_tolerate_formatting(monkeypatch):
    monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", PK)
    monkeypatch.setenv("CLERK_AUTHORIZED_PARTIES", ' "https://VitaDrive.test/" , ')
    from vitadrive.auth import _origin
    auth = ClerkAuth.from_env()
    assert [_origin(p) for p in auth.authorized_parties] == ["https://vitadrive.test"]
