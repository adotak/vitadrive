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
    assert body["radius_km"] == 10 and len(queries) == 1 and '"shop"="tyres"' in queries[0]


def test_search_widens_radius_when_sparse(client, monkeypatch):
    queries = []
    monkeypatch.setattr(nearby, "overpass", lambda q: queries.append(q) or FAKE[:1])
    body = client.get(URL, params={"lat": 51.5, "lon": -0.12, "kind": "mechanic"}, headers=ALICE).json()
    assert body["kind"] == "mechanic" and body["radius_km"] == 40 and len(queries) == 2


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

    def offline(url, data=None, timeout=0):
        raise TimeoutError

    monkeypatch.setattr(nearby, "overpass", down)
    monkeypatch.setattr(nearby, "_get_json", offline)
    monkeypatch.setattr(nearby, "NOMINATIM_DELAY_S", 0)
    assert client.get(URL, params={"lat": 1, "lon": 2}, headers=ALICE).status_code == 502
    assert client.get(URL, params={"kind": "spaceport"}, headers=ALICE).status_code == 422
    assert client.get(URL, params={"lat": 91, "lon": 0}, headers=ALICE).status_code == 422


def test_other_users_cannot_use_vehicle(client):
    assert client.get(URL, headers={"Authorization": "mallory"}).status_code == 404


def test_distance():
    assert nearby.distance_km(51.5072, -0.1276, 48.8566, 2.3522) == pytest.approx(344, abs=2)


def test_overpass_uses_first_working_mirror(monkeypatch):
    import time

    def fake(url, data=None, timeout=0):
        if url == nearby.OVERPASS_URLS[0]:
            raise TimeoutError
        if url == nearby.OVERPASS_URLS[1]:
            time.sleep(0.3)
            return {"elements": ["slow"]}
        if url == nearby.OVERPASS_URLS[2]:
            return "<html>busy</html>"
        return {"elements": ["fast"]}

    monkeypatch.setattr(nearby, "_get_json", fake)
    assert nearby.overpass("q") == ["fast"]


def test_overpass_all_mirrors_down(monkeypatch):
    def down(url, data=None, timeout=0):
        raise TimeoutError

    monkeypatch.setattr(nearby, "_get_json", down)
    monkeypatch.setattr(nearby, "OVERPASS_RETRY_DELAY_S", 0)
    with pytest.raises(nearby.MapServiceError, match="busy"):
        nearby.overpass("q")


def test_overpass_retries_when_mirrors_are_busy(monkeypatch):
    calls = []

    def flaky(url, data=None, timeout=0):
        calls.append(url)
        if len(calls) <= len(nearby.OVERPASS_URLS):
            raise TimeoutError
        return {"elements": ["ok"]}

    monkeypatch.setattr(nearby, "_get_json", flaky)
    monkeypatch.setattr(nearby, "OVERPASS_RETRY_DELAY_S", 0)
    assert nearby.overpass("q") == ["ok"]


def test_falls_back_to_nominatim_when_overpass_is_busy(client, monkeypatch):
    def down(q):
        raise nearby.MapServiceError("busy")

    urls = []

    def nominatim(url, data=None, timeout=0):
        urls.append(url)
        return [
            {"lat": "51.508", "lon": "-0.128", "name": "Kwik Fit", "extratags": {"phone": "+44 20 0000"},
             "address": {"house_number": "1", "road": "Strand", "city": "London"}},
            {"lat": "51.51", "lon": "-0.13", "name": "", "extratags": None, "address": {}},
            {"lat": "51.9", "lon": "-0.13", "name": "Outside the radius"},
        ]

    monkeypatch.setattr(nearby, "overpass", down)
    monkeypatch.setattr(nearby, "_get_json", nominatim)
    monkeypatch.setattr(nearby, "NOMINATIM_DELAY_S", 0)
    body = client.get(URL, params={"lat": 51.5072, "lon": -0.1276}, headers=ALICE).json()
    assert [p["name"] for p in body["places"]] == ["Kwik Fit", "Tire shop"]
    assert body["places"][0]["address"] == "1 Strand, London" and body["places"][0]["phone"] == "+44 20 0000"
    assert body["radius_km"] == 40 and len(urls) == 3 and "%5Bshop%3Dtyres%5D" in urls[0]


def test_keeps_close_results_when_widening_fails(client, monkeypatch):
    calls = []

    def first_only(q):
        calls.append(q)
        if len(calls) > 1:
            raise nearby.MapServiceError("busy")
        return FAKE[:1]

    monkeypatch.setattr(nearby, "overpass", first_only)
    monkeypatch.setattr(nearby, "_get_json", lambda *a, **k: pytest.fail("no fallback when some places were found"))
    body = client.get(URL, params={"lat": 51.5072, "lon": -0.1276}, headers=ALICE).json()
    assert [p["name"] for p in body["places"]] == ["Far Tyres"] and body["radius_km"] == 10
