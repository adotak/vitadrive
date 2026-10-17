from __future__ import annotations

import pytest
from fastapi.responses import Response
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import select

from vitadrive.api import create_app
from vitadrive.security import build_csp
from vitadrive.storage import alerts_t, fuel_logs_t, readings_daily_t, readings_t, services_t, vehicles_t

VIN = "VDASECURITY000001"
BOB_VIN = "VDBOBSEC000000001"
CAR = {"vin": VIN, "make": "Toyota", "model": "Corolla", "year": 2023, "powertrain": "ice",
       "tank_capacity_l": 50}
BOB_CAR = {**CAR, "vin": BOB_VIN}
ALICE = {"Authorization": "alice"}
BOB = {"Authorization": "bob"}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "geolocation=(self), camera=(), microphone=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin-allow-popups",
}


class HeaderAuth:
    def __init__(self, frontend_api: str | None = None):
        self.frontend_api = frontend_api
        self.enabled = frontend_api is not None
        self.publishable_key = "pk_test" if self.enabled else None

    def user_id(self, authorization):
        return authorization or "local"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    app = create_app(str(tmp_path / "security.db"), auth=HeaderAuth())
    with TestClient(app) as test_client:
        yield test_client
    app.state.vitadrive.store.close()


def _assert_security_headers(response, *, csp=True):
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value
    assert ("Content-Security-Policy" in response.headers) is csp


def test_security_headers_cover_dashboard_health_errors_and_static_files(client):
    responses = [
        client.get("/"),
        client.get("/api/health"),
        client.get("/missing-security-route"),
        client.get("/static/icon-192.png"),
    ]
    assert [response.status_code for response in responses] == [200, 200, 404, 200]
    for response in responses:
        _assert_security_headers(response)


def test_static_assets_are_cached_but_document_worker_and_api_are_not(client):
    static = client.get("/static/dashboard.css")
    assert static.headers["Cache-Control"] == "public, max-age=3600"

    for path in ("/", "/static/index.html", "/static/sw.js", "/api/health"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers.get("Cache-Control") != "public, max-age=3600"


def test_csp_allows_dashboard_resources():
    directives = {
        item.strip().split(" ", 1)[0]: item.strip().split(" ", 1)[1]
        for item in build_csp(None).split(";")
    }
    assert directives["default-src"] == "'self'"
    assert directives["base-uri"] == "'self'"
    assert directives["object-src"] == "'none'"
    assert directives["frame-ancestors"] == "'none'"
    assert directives["form-action"] == "'self'"
    assert "https://unpkg.com" in directives["script-src"]
    assert "https://challenges.cloudflare.com" in directives["script-src"]
    assert "'unsafe-inline'" not in directives["script-src"]
    assert "https://unpkg.com" in directives["style-src"]
    assert "https://unpkg.com" in directives["img-src"]
    assert "https://tile.openstreetmap.org" in directives["img-src"]
    assert "https://*.tile.openstreetmap.org" in directives["img-src"]
    assert "https://upload.wikimedia.org" in directives["img-src"]
    assert "https://thumb.wikimedia.org" in directives["img-src"]
    assert "https://img.clerk.com" in directives["img-src"]
    assert directives["font-src"] == "'self' data:"
    assert directives["media-src"] == "'self'"
    assert directives["manifest-src"] == "'self'"
    assert directives["worker-src"] == "'self' blob:"
    assert directives["connect-src"] == "'self'"
    assert "https://challenges.cloudflare.com" in directives["frame-src"]


def test_docs_skip_csp_but_keep_other_security_headers(client):
    for path in ("/docs", "/redoc"):
        response = client.get(path)
        assert response.status_code == 200
        _assert_security_headers(response, csp=False)


def test_csp_has_clerk_sources_only_when_a_frontend_api_is_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    without_clerk = create_app(str(tmp_path / "no-clerk.db"), auth=HeaderAuth())
    with_clerk = create_app(
        str(tmp_path / "clerk.db"),
        auth=HeaderAuth("concrete-stallion-1253.clerk.accounts.dev"),
    )
    try:
        assert "https://concrete-stallion-1253.clerk.accounts.dev" not in build_csp(None)
        assert "https://clerk-telemetry.com" not in build_csp(None)
        assert "https://*.protect.clerk.com" not in build_csp(None)
        with TestClient(without_clerk) as no_clerk_client, TestClient(with_clerk) as clerk_client:
            plain = no_clerk_client.get("/").headers["Content-Security-Policy"]
            configured = clerk_client.get("/").headers["Content-Security-Policy"]
        assert "https://concrete-stallion-1253.clerk.accounts.dev" not in plain
        assert "https://concrete-stallion-1253.clerk.accounts.dev" in configured
        assert "https://clerk-telemetry.com" in configured
        assert "https://*.clerk-telemetry.com" in configured
        assert "https://*.protect.clerk.com" in configured
        assert "https://*.protect.clerk.com:*" in configured
    finally:
        without_clerk.state.vitadrive.store.close()
        with_clerk.state.vitadrive.store.close()


def test_hsts_requires_https_or_forwarded_https(client):
    response = client.get("/")
    assert "Strict-Transport-Security" not in response.headers

    response = client.get("https://testserver/")
    assert response.headers["Strict-Transport-Security"] == "max-age=63072000; includeSubDomains"

    response = client.get("/", headers={"X-Forwarded-Proto": "https"})
    assert response.headers["Strict-Transport-Security"] == "max-age=63072000; includeSubDomains"


def test_security_middleware_preserves_route_headers(client):
    client.app.add_api_route(
        "/_security-custom",
        lambda: Response(headers={
            "Content-Security-Policy": "route-policy",
            "X-Content-Type-Options": "route-value",
            "Strict-Transport-Security": "max-age=1",
        }),
        include_in_schema=False,
    )
    response = client.get("/_security-custom")
    assert response.headers["Content-Security-Policy"] == "route-policy"
    assert response.headers["X-Content-Type-Options"] == "route-value"
    assert response.headers["Strict-Transport-Security"] == "max-age=1"


def test_cors_is_disabled_by_default(client):
    response = client.get("/api/health", headers={"Origin": "https://foreign.example"})
    assert "Access-Control-Allow-Origin" not in response.headers


def test_cors_allows_only_configured_origins_and_exposes_headers(tmp_path, monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://allowed.example, *")
    app = create_app(str(tmp_path / "cors.db"), auth=HeaderAuth())
    try:
        with TestClient(app) as test_client:
            allowed = test_client.get("/api/health", headers={"Origin": "https://allowed.example"})
            foreign = test_client.get("/api/health", headers={"Origin": "https://allowed.example.evil"})
            preflight = test_client.options(
                "/api/health",
                headers={
                    "Origin": "https://allowed.example",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "Authorization, Content-Type, X-API-Key, X-Request-ID",
                },
            )
        assert allowed.headers["Access-Control-Allow-Origin"] == "https://allowed.example"
        assert "Access-Control-Allow-Credentials" not in allowed.headers
        exposed = {header.strip().lower() for header in allowed.headers["Access-Control-Expose-Headers"].split(",")}
        assert exposed == {"x-request-id", "retry-after"}
        assert "Access-Control-Allow-Origin" not in foreign.headers
        assert preflight.status_code == 200
        methods = {method.strip() for method in preflight.headers["Access-Control-Allow-Methods"].split(",")}
        assert methods == {"DELETE", "GET", "POST", "PUT"}
        allowed_headers = {
            header.strip().lower()
            for header in preflight.headers["Access-Control-Allow-Headers"].split(",")
        }
        assert {"authorization", "content-type", "x-api-key", "x-request-id"} <= allowed_headers
    finally:
        app.state.vitadrive.store.close()


def test_cors_wildcard_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "*")
    app = create_app(str(tmp_path / "cors-wildcard.db"), auth=HeaderAuth())
    try:
        with TestClient(app) as test_client:
            response = test_client.get("/api/health", headers={"Origin": "https://foreign.example"})
        assert "Access-Control-Allow-Origin" not in response.headers
    finally:
        app.state.vitadrive.store.close()


OWNER_ROUTE_CASES = {
    ("PUT", "/api/vehicles/{vin}"): {"json": CAR},
    ("DELETE", "/api/vehicles/{vin}"): {},
    ("POST", "/api/vehicles/{vin}/api-key"): {},
    ("GET", "/api/vehicles/{vin}/readings"): {},
    ("GET", "/api/vehicles/{vin}/alerts"): {},
    ("GET", "/api/vehicles/{vin}/services"): {},
    ("POST", "/api/vehicles/{vin}/services"): {
        "json": {"item": "oil_change", "performed_on": "2025-01-01", "odometer_km": 1000},
    },
    ("GET", "/api/vehicles/{vin}/fuel"): {},
    ("POST", "/api/vehicles/{vin}/fuel"): {
        "json": {"filled_on": "2025-01-01", "odometer_km": 1000, "amount": 10, "cost": 20},
    },
    ("DELETE", "/api/vehicles/{vin}/fuel/{log_id}"): {},
    ("GET", "/api/vehicles/{vin}/costs"): {},
    ("GET", "/api/vehicles/{vin}/maintenance"): {},
    ("GET", "/api/vehicles/{vin}/maintenance/calendar.ics"): {},
    ("GET", "/api/vehicles/{vin}/range"): {},
    ("GET", "/api/vehicles/{vin}/report"): {},
    ("POST", "/api/vehicles/{vin}/ask"): {"json": {"question": "What needs attention?"}},
    ("GET", "/api/vehicles/{vin}/nearby"): {},
    ("GET", "/api/vehicles/{vin}/photo"): {},
    ("GET", "/api/vehicles/{vin}/recalls"): {},
}
VIN_ROUTE_ALLOWLIST = {("POST", "/api/vehicles/{vin}/readings")}


def _vin_routes(app):
    return {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute) and "{vin}" in route.path
        for method in route.methods
    }


def test_vin_route_coverage_guard(client):
    assert _vin_routes(client.app) == set(OWNER_ROUTE_CASES) | VIN_ROUTE_ALLOWLIST


def _alice_data_snapshot(store):
    tables = (vehicles_t, readings_t, readings_daily_t, alerts_t, services_t, fuel_logs_t)
    with store.transaction() as connection:
        return {
            table.name: tuple(sorted(
                (tuple(row) for row in connection.execute(
                    select(table).where(table.c.vin == VIN),
                )),
                key=repr,
            ))
            for table in tables
        }


def test_every_owner_scoped_vin_route_rejects_bob_without_changing_alice_data(client):
    client.put(f"/api/vehicles/{VIN}", json=CAR, headers=ALICE).raise_for_status()
    alice_key = client.post(f"/api/vehicles/{VIN}/api-key", headers=ALICE).json()["api_key"]
    client.post(
        f"/api/vehicles/{VIN}/readings",
        json={"vin": VIN, "odometer_km": 1000},
        headers={"X-API-Key": alice_key},
    ).raise_for_status()
    client.post(
        f"/api/vehicles/{VIN}/services",
        json={"item": "oil_change", "performed_on": "2025-01-01", "odometer_km": 900},
        headers=ALICE,
    ).raise_for_status()
    fuel = client.post(
        f"/api/vehicles/{VIN}/fuel",
        json={"filled_on": "2025-01-02", "odometer_km": 950, "amount": 10, "cost": 20},
        headers=ALICE,
    )
    assert fuel.status_code == 201
    fuel_id = fuel.json()["id"]
    client.put(f"/api/vehicles/{BOB_VIN}", json=BOB_CAR, headers=BOB).raise_for_status()
    bob_key = client.post(f"/api/vehicles/{BOB_VIN}/api-key", headers=BOB).json()["api_key"]

    store = client.app.state.vitadrive.store
    before = _alice_data_snapshot(store)
    for (method, route_path), request_args in OWNER_ROUTE_CASES.items():
        path = route_path.replace("{vin}", VIN).replace("{log_id}", str(fuel_id))
        response = client.request(method, path, headers=BOB, **request_args)
        if (method, route_path) == ("PUT", "/api/vehicles/{vin}"):
            assert response.status_code == 409, response.text
        else:
            assert response.status_code in {401, 403, 404}, (method, route_path, response.text)
        assert _alice_data_snapshot(store) == before

    reading = {"vin": VIN, "odometer_km": 1001}
    owner_token_attempt = client.post(
        f"/api/vehicles/{VIN}/readings",
        json=reading,
        headers=BOB,
    )
    bob_key_attempt = client.post(
        f"/api/vehicles/{VIN}/readings",
        json=reading,
        headers={"X-API-Key": bob_key},
    )
    wrong_key_attempt = client.post(
        f"/api/vehicles/{VIN}/readings",
        json=reading,
        headers={"X-API-Key": "vd_wrong"},
    )
    assert owner_token_attempt.status_code == 404
    assert bob_key_attempt.status_code == wrong_key_attempt.status_code == 401
    assert _alice_data_snapshot(store) == before
