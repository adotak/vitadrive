from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_docker_image_runs_as_non_root_and_has_a_healthcheck() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert re.search(r"(?m)^USER 10001:10001$", dockerfile)
    assert "HEALTHCHECK" in dockerfile
    assert "DATABASE_URL=/data/vitadrive.db" in dockerfile


def test_runtime_requirements_do_not_include_torch_or_numpy() -> None:
    requirements = (ROOT / "requirements.txt").read_text()
    assert not re.search(r"(?im)^\s*(torch|numpy)(?:\s|[<=>~!;[]|$)", requirements)


def test_compose_app_waits_for_postgres_and_uses_its_database() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    services = compose["services"]
    app = services["app"]

    assert app["depends_on"]["db"]["condition"] == "service_healthy"
    assert app["environment"]["DATABASE_URL"] == "postgresql://vitadrive:vitadrive@db:5432/vitadrive"
    assert not services["db"].get("ports")
