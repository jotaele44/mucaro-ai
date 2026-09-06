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
    ANSWER_TRACE_EVAL_OK,
    build_answer_trace_eval_report,
    format_answer_trace_eval_text,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate cited-answer traceability against a committed fixture.")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_ANSWER_TRACE_FIXTURE, help="Answer-trace fixture path.")
    parser.add_argument("--min-case-pass-rate", type=float, default=1.0, help="Minimum fixture case pass rate.")
    parser.add_argument(
        "--allow-no-negative-cases",
        action="store_true",
        help="Do not require at least one negative citation case in the fixture.",
    )
    parser.add_argument("--format", choices=["json", "text"], default="text", help="Output format.")
    args = parser.parse_args()
    report = build_answer_trace_eval_report(
        fixture_path=args.fixture,
        min_case_pass_rate=args.min_case_pass_rate,
        require_negative_cases=not args.allow_no_negative_cases,
    )
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for line in format_answer_trace_eval_text(report):
            print(line)
    return 0 if report["status"] == ANSWER_TRACE_EVAL_OK else 2


if __name__ == "__main__":
    raise SystemExit(main())
