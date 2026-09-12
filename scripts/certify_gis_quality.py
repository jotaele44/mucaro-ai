#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from backend.app.gis_eval import (  # noqa: E402
    DEFAULT_GIS_EVAL_FIXTURE,
    GIS_QUALITY_OK,
    GIS_QUALITY_PROFILE,
    build_gis_quality_certification,
    format_gis_quality_certification_text,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify GIS fixture quality for the current release head.")
    parser.add_argument("--profile", default=GIS_QUALITY_PROFILE, help="Named GIS-quality profile to record.")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_GIS_EVAL_FIXTURE, help="GIS evaluation fixture path.")
    parser.add_argument("--format", choices=["json", "text"], default="text", help="Output format.")
    args = parser.parse_args()

    command = [sys.executable, "scripts/certify_gis_quality.py"]
    if args.profile != GIS_QUALITY_PROFILE:
        command.extend(["--profile", args.profile])
    if args.fixture != DEFAULT_GIS_EVAL_FIXTURE:
        command.extend(["--fixture", str(args.fixture)])

    report = build_gis_quality_certification(
        ROOT,
        profile=args.profile,
        fixture_path=args.fixture,
        command=command,
    )
    if args.format == "json":
        import json

        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(report["status"])
        for line in format_gis_quality_certification_text(report):
            print(line)
    return 0 if report["status"] == GIS_QUALITY_OK else 2


if __name__ == "__main__":
    raise SystemExit(main())
