from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from runpy import run_path

_badge_module = run_path(str(Path(__file__).resolve().parents[1] / "scripts" / "coverage_badge.py"))
coverage_badge = _badge_module["coverage_badge"]
main = _badge_module["main"]


def test_coverage_badge_color_thresholds() -> None:
    assert coverage_badge(Decimal("90"))["color"] == "brightgreen"
    assert coverage_badge(Decimal("85"))["color"] == "green"
    assert coverage_badge(Decimal("75"))["color"] == "yellow"
    assert coverage_badge(Decimal("74.9"))["color"] == "red"


def test_coverage_badge_rounds_half_up() -> None:
    assert coverage_badge(Decimal("87.4"))["message"] == "87%"
    assert coverage_badge(Decimal("87.5"))["message"] == "88%"
    assert coverage_badge(Decimal("89.9")) == {
        "schemaVersion": 1,
        "label": "coverage",
        "message": "90%",
        "color": "green",
    }


def test_main_reads_xml_and_writes_badge(tmp_path) -> None:
    coverage_xml = tmp_path / "coverage.xml"
    coverage_xml.write_text('<coverage line-rate="0.875"/>')
    output = tmp_path / "badge" / "coverage.json"

    main([str(coverage_xml), str(output)])

    assert json.loads(output.read_text()) == {
        "schemaVersion": 1,
        "label": "coverage",
        "message": "88%",
        "color": "green",
    }
