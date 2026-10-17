"""Generate a shields.io endpoint badge from a Cobertura coverage report."""
from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Sequence


def coverage_badge(percentage: Decimal) -> dict[str, int | str]:
    rounded = int(percentage.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    color = (
        "brightgreen"
        if percentage >= 90
        else "green"
        if percentage >= 85
        else "yellow"
        if percentage >= 75
        else "red"
    )
    return {"schemaVersion": 1, "label": "coverage", "message": f"{rounded}%", "color": color}


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("coverage_xml", type=Path)
    parser.add_argument("output_json", type=Path)
    args = parser.parse_args(argv)

    coverage = ET.parse(args.coverage_xml).getroot().attrib["line-rate"]
    badge = coverage_badge(Decimal(coverage) * 100)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(badge, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
