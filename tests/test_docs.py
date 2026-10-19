from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"!?\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+[^)]*)?\)")
ENV_READ = re.compile(
    r"""(?:os\.environ(?:\.get)?\(|os\.getenv\(|_env\()\s*['"]([A-Z][A-Z0-9_]*)['"]"""
)
ENV_INDEX = re.compile(r"""os\.environ\[\s*['"]([A-Z][A-Z0-9_]*)['"]\s*\]""")


def _markdown_files() -> list[Path]:
    return sorted({*ROOT.glob("*.md"), *ROOT.joinpath("docs").rglob("*.md")})


def _local_targets(markdown: Path) -> list[Path]:
    text = markdown.read_text(encoding="utf-8")
    text = re.sub(r"(?s)```.*?```|~~~.*?~~~", "", text)
    targets: list[Path] = []
    for match in LINK.finditer(text):
        target = (match.group(1) or match.group(2) or "").strip()
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue
        path = unquote(parsed.path)
        targets.append((ROOT / path.lstrip("/")) if path.startswith("/") else markdown.parent / path)
    return targets


def test_relative_markdown_links_and_images_resolve() -> None:
    missing = [
        f"{markdown.relative_to(ROOT)} -> {target.relative_to(ROOT) if target.is_relative_to(ROOT) else target}"
        for markdown in _markdown_files()
        for target in _local_targets(markdown)
        if not target.is_file()
    ]
    assert not missing, "Broken relative Markdown links or images:\n" + "\n".join(missing)


def test_readme_contains_a_mermaid_diagram() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert re.search(r"(?ms)^```mermaid\s*\n.+?^```\s*$", readme)


def test_each_adr_is_indexed_and_has_nygard_sections() -> None:
    adr_dir = ROOT / "docs" / "adr"
    index = (adr_dir / "README.md").read_text(encoding="utf-8")
    required = ("## Status", "## Context", "## Decision", "## Consequences")
    decisions = sorted(path for path in adr_dir.glob("0[0-9][0-9][0-9]-*.md"))

    assert len(decisions) == 8
    for decision in decisions:
        text = decision.read_text(encoding="utf-8")
        assert all(heading in text for heading in required), decision.name
        assert f"({decision.name})" in index, f"{decision.name} is missing from the ADR index"


def test_architecture_environment_reference_matches_code() -> None:
    architecture = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    section = re.search(r"(?ms)^## Environment variables\n(.*?)(?=^## |\Z)", architecture)
    assert section is not None
    documented = set(re.findall(r"(?m)^\| `([A-Z][A-Z0-9_]*)` \|", section.group(1)))

    sources = [*ROOT.joinpath("vitadrive").rglob("*.py"), ROOT / "app.py"]
    code = "\n".join(path.read_text(encoding="utf-8") for path in sources)
    read = set(ENV_READ.findall(code)) | set(ENV_INDEX.findall(code))

    assert documented == read, (
        f"Missing from architecture table: {sorted(read - documented)}; "
        f"not read by code: {sorted(documented - read)}"
    )
