import urllib.error
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from vitadrive import webinfo
from vitadrive.api import create_app

CAR = {"vin": "VDWEBINFO0000001", "make": "Toyota", "model": "Corolla", "year": 2023, "powertrain": "ice"}
BASE = f"/api/vehicles/{CAR['vin']}"
ALICE = {"Authorization": "alice"}

NHTSA = {"Count": 2, "results": [
    {"NHTSACampaignNumber": "23V480000", "ReportReceivedDate": "12/07/2023", "Component": "STEERING:COLUMN",
     "Summary": "Air bag may not deploy.", "Consequence": "Injury risk.", "Remedy": "Dealers replace it.",
     "parkIt": False, "parkOutSide": False, "overTheAirUpdate": False},
    {"NHTSACampaignNumber": "24V100000", "ReportReceivedDate": "05/02/2024", "Component": "FUEL SYSTEM, GASOLINE",
     "Summary": "Fuel pump may fail.", "parkIt": True, "parkOutSide": True, "overTheAirUpdate": False},
]}
SUMMARY = {"type": "standard", "title": "Toyota Corolla", "description": "Japanese compact car",
           "thumbnail": {"source": "https://upload.wikimedia.org/wikipedia/commons/thumb/f/fe/C.jpg/330px-C.jpg"},
           "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Toyota_Corolla"}}}


class HeaderAuth:
    enabled, publishable_key, frontend_api = True, None, None

    def user_id(self, authorization):
        return authorization or "anon"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(webinfo, "_cache", {})
    client = TestClient(create_app(str(tmp_path / "web.db"), auth=HeaderAuth()))
    client.put(BASE, json=CAR, headers=ALICE).raise_for_status()
    return client


def fake_web(monkeypatch, routes):
    """Serve canned JSON per URL prefix; a value that is an exception gets raised instead."""
    calls = []

    def get(url, timeout=10):
        calls.append(url)
        for prefix, val in routes.items():
            if url.startswith(prefix):
                if isinstance(val, Exception):
                    raise val
                return val
        pytest.fail(f"unexpected request {url}")

    monkeypatch.setattr(webinfo, "_get_json", get)
    return calls


def http_error(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, None)


def test_recalls_query_vehicle_and_sort_newest_first(client, monkeypatch):
    calls = fake_web(monkeypatch, {webinfo.NHTSA_RECALLS_URL: NHTSA})
    body = client.get(f"{BASE}/recalls", headers=ALICE).json()
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(calls[0]).query)
    assert q == {"make": ["Toyota"], "model": ["Corolla"], "modelYear": ["2023"]}
    assert [r["campaign"] for r in body["recalls"]] == ["24V100000", "23V480000"]
    first = body["recalls"][0]
    assert first["reported_on"] == "2024-02-05" and first["park_it"] and first["component"] == "Fuel System, Gasoline"
    assert body["recalls"][1]["component"] == "Steering · Column"


def test_recalls_are_cached(client, monkeypatch):
    calls = fake_web(monkeypatch, {webinfo.NHTSA_RECALLS_URL: NHTSA})
    client.get(f"{BASE}/recalls", headers=ALICE)
    client.get(f"{BASE}/recalls", headers=ALICE)
    assert len(calls) == 1


def test_no_recalls(client, monkeypatch):
    fake_web(monkeypatch, {webinfo.NHTSA_RECALLS_URL: {"Count": 0, "results": []}})
    assert client.get(f"{BASE}/recalls", headers=ALICE).json()["recalls"] == []


def test_unknown_model_is_no_recalls(client, monkeypatch):
    fake_web(monkeypatch, {webinfo.NHTSA_RECALLS_URL: http_error(400)})
    assert client.get(f"{BASE}/recalls", headers=ALICE).json()["recalls"] == []


def test_recall_service_down_is_502_and_not_cached(client, monkeypatch):
    fake_web(monkeypatch, {webinfo.NHTSA_RECALLS_URL: TimeoutError()})
    r = client.get(f"{BASE}/recalls", headers=ALICE)
    assert r.status_code == 502 and "recall database" in r.json()["detail"]
    fake_web(monkeypatch, {webinfo.NHTSA_RECALLS_URL: NHTSA})
    assert len(client.get(f"{BASE}/recalls", headers=ALICE).json()["recalls"]) == 2


def test_photo_from_exact_article(client, monkeypatch):
    calls = fake_web(monkeypatch, {webinfo.WIKI_SUMMARY_URL: SUMMARY})
    body = client.get(f"{BASE}/photo", headers=ALICE).json()
    assert calls == [webinfo.WIKI_SUMMARY_URL + "Toyota_Corolla"]
    assert body["image"].endswith("/960px-C.jpg") and body["page_url"].endswith("/Toyota_Corolla")


def test_photo_falls_back_to_search(client, monkeypatch):
    found = {"query": {"pages": {"1": {"title": "Toyota Corolla (E210)", "fullurl": "https://en.wikipedia.org/wiki/E210",
                                       "thumbnail": {"source": "https://upload.wikimedia.org/x/960px-E.jpg"}}}}}
    fake_web(monkeypatch, {webinfo.WIKI_SUMMARY_URL: http_error(404), webinfo.WIKI_API_URL: found})
    assert client.get(f"{BASE}/photo", headers=ALICE).json()["title"] == "Toyota Corolla (E210)"


def test_photo_ignores_unrelated_search_hit(client, monkeypatch):
    other = {"query": {"pages": {"1": {"title": "Corolla (plant)", "thumbnail": {"source": "https://x/y.jpg"}}}}}
    fake_web(monkeypatch, {webinfo.WIKI_SUMMARY_URL: {"type": "disambiguation"}, webinfo.WIKI_API_URL: other})
    assert client.get(f"{BASE}/photo", headers=ALICE).json() is None


def test_photo_is_null_when_wikipedia_is_down(client, monkeypatch):
    fake_web(monkeypatch, {webinfo.WIKI_SUMMARY_URL: TimeoutError()})
    r = client.get(f"{BASE}/photo", headers=ALICE)
    assert r.status_code == 200 and r.json() is None


@pytest.mark.parametrize("path", ["photo", "recalls"])
def test_other_users_cannot_look_up_my_car(client, monkeypatch, path):
    fake_web(monkeypatch, {})
    assert client.get(f"{BASE}/{path}", headers={"Authorization": "mallory"}).status_code == 404
