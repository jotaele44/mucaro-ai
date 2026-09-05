#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.retrieval import MAX_TOP_K, MIN_TOP_K  # noqa: E402
from backend.app.retrieval_quality_certification import (  # noqa: E402
    RETRIEVAL_QUALITY_OK,
    RETRIEVAL_QUALITY_PROFILE,
    build_retrieval_quality_certification,
    format_retrieval_quality_certification_text,
)


def parse_top_k(value: str) -> int:
    try:
        top_k = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("TOP_K_MUST_BE_INTEGER") from exc
    if top_k < MIN_TOP_K or top_k > MAX_TOP_K:
        raise argparse.ArgumentTypeError(f"TOP_K_RANGE:{MIN_TOP_K}-{MAX_TOP_K}")
    return top_k


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Certify Mucaro AI retrieval quality against the release fixture profile."
    )
    parser.add_argument("--profile", default=RETRIEVAL_QUALITY_PROFILE, help="Named retrieval-quality profile to record.")
    parser.add_argument("--top-k", type=parse_top_k, default=5, help=f"Maximum retrieved chunks to score ({MIN_TOP_K}-{MAX_TOP_K}).")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format.")
    args = parser.parse_args()

    command = [sys.executable, "scripts/certify_retrieval_quality.py"]
    if args.profile != RETRIEVAL_QUALITY_PROFILE:
        command.extend(["--profile", args.profile])
    if args.top_k != 5:
        command.extend(["--top-k", str(args.top_k)])
    report = build_retrieval_quality_certification(
        ROOT,
        profile=args.profile,
        top_k=args.top_k,
        command=command,
    )
    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for line in format_retrieval_quality_certification_text(report):
            print(line)
    return 0 if report["status"] == RETRIEVAL_QUALITY_OK else 2


if __name__ == "__main__":
    raise SystemExit(main())
