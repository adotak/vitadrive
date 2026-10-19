from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

import pytest
from playwright.sync_api import Page, Route

ROOT = Path(__file__).resolve().parents[1]

PHOTO = {
    "title": "Demo vehicle",
    "image": "data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=",
    "page_url": "https://example.test/demo-vehicle",
}
RECALLS = {
    "make": "Demo",
    "model": "Vehicle",
    "year": 2024,
    "source": "E2E fixture",
    "lookup_url": "https://www.nhtsa.gov/recalls",
    "recalls": [],
}
NEARBY = {
    "lat": 51.5074,
    "lon": -0.1278,
    "kind": "mechanic",
    "label": "Mechanic",
    "radius_km": 25,
    "needs": [],
    "places": [
        {
            "name": "E2E Garage",
            "kind": "mechanic",
            "lat": 51.51,
            "lon": -0.12,
            "distance_km": 1.2,
            "address": "1 Test Street",
        },
    ],
}


@pytest.fixture(scope="session")
def server_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    database = tmp_path_factory.mktemp("vitadrive-e2e") / "demo.db"
    env = os.environ.copy()
    for key in tuple(env):
        if key.startswith("CLERK_") or key in {"OPENAI_API_KEY", "SENTRY_DSN"}:
            env.pop(key)
    env["DATABASE_URL"] = str(database)
    env["VITADRIVE_RATE_LIMITS"] = "0"

    seeded = subprocess.run(
        [
            sys.executable,
            "-m",
            "vitadrive.cli",
            "--db",
            str(database),
            "demo",
            "--days",
            "60",
            "--seed",
            "7",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if seeded.returncode:
        pytest.fail(f"Could not seed the E2E database:\n{seeded.stdout}\n{seeded.stderr}")

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    with tempfile.TemporaryFile(mode="w+t") as server_log:
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "vitadrive.api:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=ROOT,
            env=env,
            stdout=server_log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if server.poll() is not None:
                server_log.seek(0)
                pytest.fail(f"E2E server exited early:\n{server_log.read()}")
            try:
                with urlopen(f"{url}/api/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except (OSError, URLError, TimeoutError):
                pass
            time.sleep(0.1)
        else:
            server.terminate()
            server.wait(timeout=10)
            server_log.seek(0)
            pytest.fail(f"E2E server did not become healthy:\n{server_log.read()}")

        try:
            yield url
        finally:
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


@pytest.fixture(autouse=True)
def browser_guards(page: Page) -> Iterator[None]:
    errors: list[str] = []
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: errors.append(str(error)))

    def route_request(route: Route) -> None:
        request_url = urlsplit(route.request.url)
        path = request_url.path
        if request_url.hostname == "tile.openstreetmap.org":
            route.fulfill(status=204, body="")
        elif path.endswith("/photo"):
            route.fulfill(json=PHOTO)
        elif path.endswith("/recalls"):
            route.fulfill(json=RECALLS)
        elif path.endswith("/nearby"):
            route.fulfill(json=NEARBY)
        else:
            route.continue_()

    page.route("**/*", route_request)
    yield
    assert not errors, "Browser console errors:\n" + "\n".join(errors)
