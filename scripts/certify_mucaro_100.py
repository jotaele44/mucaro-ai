#!/usr/bin/env python3
"""Report or strictly enforce the MUCARO_100_V1 terminal certification gate."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.mucaro_100 import compute_mucaro_100  # noqa: E402


def render_text(report: dict) -> str:
    capability = report["capability"]
    release = report["release"]
    lines = [
        report["status"],
        f"CAPABILITY_PASS {capability['pass']}/{capability['denominator']} strict={capability['strict_score_percent']:.1f}",
        (
            f"RELEASE_PASS {release['pass']}/{release['applicable_denominator']} "
            f"applicable strict={release['strict_score_percent']:.1f} "
            f"(total={release['denominator']} not_applicable={release['not_applicable']})"
        ),
    ]
    for name, value in report["conditions"].items():
        lines.append(f"{'PASS' if value else 'OPEN'} {name}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 2 unless both frozen denominators satisfy their terminal PASS rules",
    )
    args = parser.parse_args()

    report = compute_mucaro_100(ROOT)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_text(report))
    return 2 if args.strict and not report["certified"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
