from __future__ import annotations

import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

from vitadrive import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_package_version_matches_pyproject() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert __version__ == project["project"]["version"]


def test_dockerfile_pin_matches_package_version() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    pins = re.findall(r"\bvitadrive==([0-9]+\.[0-9]+\.[0-9]+)\b", dockerfile)
    assert pins == [__version__]


def test_first_released_changelog_heading_has_a_link_reference() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    released_headings = [
        line
        for line in changelog.splitlines()
        if line.startswith("## [") and line != "## [Unreleased]"
    ]

    assert released_headings
    assert re.fullmatch(
        rf"## \[{re.escape(__version__)}\] - \d{{4}}-\d{{2}}-\d{{2}}",
        released_headings[0],
    )
    assert re.search(rf"(?m)^\[{re.escape(__version__)}\]: https?://", changelog)


def test_versioned_release_notes_exist() -> None:
    assert (ROOT / "docs" / "releases" / f"v{__version__}.md").is_file()
