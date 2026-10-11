import pytest
from fastapi.testclient import TestClient

from vitadrive import nearby
from vitadrive.adapters.simulator import VehicleSimulator
from vitadrive.api import create_app
from vitadrive.models import Vehicle

CAR = {"vin": "VDNEARBY00000001", "make": "Toyota", "model": "Corolla", "year": 2023, "powertrain": "ice"}
URL = f"/api/vehicles/{CAR['vin']}/nearby"
ALICE = {"Authorization": "alice"}


class HeaderAuth:
    enabled, publishable_key, frontend_api = True, None, None

    def user_id(self, authorization):
        return authorization or "anon"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(nearby, "_cache", {})
    client = TestClient(create_app(str(tmp_path / "near.db"), auth=HeaderAuth()))
    client.put(f"/api/vehicles/{CAR['vin']}", json=CAR, headers=ALICE).raise_for_status()
    reading = next(VehicleSimulator(Vehicle(**CAR), seed=1).history(days=1))
    flat = reading.model_copy(update={"tire_pressure_fl_kpa": 150})
    client.post(f"{URL[:-7]}/readings", content=flat.model_dump_json(),
                headers={**ALICE, "Content-Type": "application/json"}).raise_for_status()
    return client


FAKE = [
    {"type": "node", "lat": 51.52, "lon": -0.12, "tags": {"name": "Far Tyres", "shop": "tyres"}},
    {"type": "way", "center": {"lat": 51.508, "lon": -0.128}, "tags": {"shop": "tyres", "brand": "Kwik Fit",
     "addr:housenumber": "1", "addr:street": "Strand", "addr:city": "London", "phone": "+44 20 0000"}},
    {"type": "node", "lat": 51.51, "lon": -0.13, "tags": {"shop": "tyres"}},
    {"type": "relation", "tags": {"name": "No location"}},
]


def test_needs_follow_alerts_without_location(client, monkeypatch):
    monkeypatch.setattr(nearby, "overpass", lambda q: pytest.fail("no lookup without a location"))
    body = client.get(URL, headers=ALICE).json()
    assert body["kind"] == "tyres" and body["needs"][0]["kind"] == "tyres"
    assert body["places"] == [] and body["lat"] is None


def test_search_sorts_by_distance(client, monkeypatch):
    queries = []
    monkeypatch.setattr(nearby, "overpass", lambda q: queries.append(q) or FAKE)
    body = client.get(URL, params={"lat": 51.5072, "lon": -0.1276}, headers=ALICE).json()
    names = [p["name"] for p in body["places"]]
    assert names == ["Kwik Fit", "Tire shop", "Far Tyres"]
    assert body["places"][0]["address"] == "1 Strand, London" and body["places"][0]["phone"] == "+44 20 0000"
    assert body["radius_km"] == 5 and len(queries) == 1 and '"shop"="tyres"' in queries[0]


def test_search_widens_radius_when_sparse(client, monkeypatch):
    queries = []
    monkeypatch.setattr(nearby, "overpass", lambda q: queries.append(q) or FAKE[:1])
    body = client.get(URL, params={"lat": 51.5, "lon": -0.12, "kind": "mechanic"}, headers=ALICE).json()
    assert body["kind"] == "mechanic" and body["radius_km"] == 40 and len(queries) == 3


def test_typed_location_and_errors(client, monkeypatch):
    monkeypatch.setattr(nearby, "overpass", lambda q: FAKE)
    monkeypatch.setattr(nearby, "geocode", lambda q: (51.5072, -0.1276))
    assert client.get(URL, params={"q": "London"}, headers=ALICE).json()["lat"] == 51.5072

    def missing(q):
        raise nearby.PlaceNotFound(f"Couldn't find '{q}'")

    monkeypatch.setattr(nearby, "geocode", missing)
    assert client.get(URL, params={"q": "Atlantis"}, headers=ALICE).status_code == 404

    def down(q):
        raise nearby.MapServiceError("Map service unavailable")

    monkeypatch.setattr(nearby, "overpass", down)
    assert client.get(URL, params={"lat": 1, "lon": 2}, headers=ALICE).status_code == 502
    assert client.get(URL, params={"kind": "spaceport"}, headers=ALICE).status_code == 422
    assert client.get(URL, params={"lat": 91, "lon": 0}, headers=ALICE).status_code == 422


def test_other_users_cannot_use_vehicle(client):
    assert client.get(URL, headers={"Authorization": "mallory"}).status_code == 404


def test_distance():
    assert nearby.distance_km(51.5072, -0.1276, 48.8566, 2.3522) == pytest.approx(344, abs=2)
