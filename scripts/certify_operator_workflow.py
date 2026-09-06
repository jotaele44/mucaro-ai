#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.operator_workflow_certification import (  # noqa: E402
    OPERATOR_WORKFLOW_OK,
    OPERATOR_WORKFLOW_PROFILE,
    build_operator_workflow_certification,
    format_operator_workflow_certification_text,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify the local operator workflow boundary.")
    parser.add_argument("--profile", default=OPERATOR_WORKFLOW_PROFILE, help="Named operator-workflow profile to record.")
    parser.add_argument("--format", choices=["json", "text"], default="text", help="Output format.")
    args = parser.parse_args()
    command = [sys.executable, "scripts/certify_operator_workflow.py"]
    if args.profile != OPERATOR_WORKFLOW_PROFILE:
        command.extend(["--profile", args.profile])
    report = build_operator_workflow_certification(ROOT, profile=args.profile, command=command)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for line in format_operator_workflow_certification_text(report):
            print(line)
    return 0 if report["status"] == OPERATOR_WORKFLOW_OK else 2


if __name__ == "__main__":
    raise SystemExit(main())
