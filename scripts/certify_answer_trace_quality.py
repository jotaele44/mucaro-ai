#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.answer_trace_eval import (  # noqa: E402
    DEFAULT_ANSWER_TRACE_FIXTURE,
    ANSWER_TRACE_QUALITY_OK,
    ANSWER_TRACE_QUALITY_PROFILE,
    build_answer_trace_quality_certification,
    format_answer_trace_quality_certification_text,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify cited-answer traceability against the release fixture profile.")
    parser.add_argument("--profile", default=ANSWER_TRACE_QUALITY_PROFILE, help="Named answer-trace profile to record.")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_ANSWER_TRACE_FIXTURE, help="Answer-trace fixture path.")
    parser.add_argument("--min-case-pass-rate", type=float, default=1.0, help="Minimum fixture case pass rate.")
    parser.add_argument(
        "--allow-no-negative-cases",
        action="store_true",
        help="Do not require at least one negative citation case in the fixture.",
    )
    parser.add_argument("--format", choices=["json", "text"], default="text", help="Output format.")
    args = parser.parse_args()
    command = [sys.executable, "scripts/certify_answer_trace_quality.py"]
    if args.profile != ANSWER_TRACE_QUALITY_PROFILE:
        command.extend(["--profile", args.profile])
    if args.fixture != DEFAULT_ANSWER_TRACE_FIXTURE:
        command.extend(["--fixture", str(args.fixture)])
    if args.min_case_pass_rate != 1.0:
        command.extend(["--min-case-pass-rate", str(args.min_case_pass_rate)])
    if args.allow_no_negative_cases:
        command.append("--allow-no-negative-cases")
    report = build_answer_trace_quality_certification(
        ROOT,
        profile=args.profile,
        fixture_path=args.fixture,
        min_case_pass_rate=args.min_case_pass_rate,
        require_negative_cases=not args.allow_no_negative_cases,
        command=command,
    )
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for line in format_answer_trace_quality_certification_text(report):
            print(line)
    return 0 if report["status"] == ANSWER_TRACE_QUALITY_OK else 2


if __name__ == "__main__":
    raise SystemExit(main())
