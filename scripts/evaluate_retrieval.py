#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from backend.app.retrieval import MAX_TOP_K, MIN_TOP_K  # noqa: E402
from backend.app.retrieval_eval import (  # noqa: E402
    DEFAULT_FIXTURE,
    build_retrieval_eval_report,
    format_retrieval_eval_text,
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
    parser = argparse.ArgumentParser(description="Evaluate deterministic retrieval strategies against the committed fixture.")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--top-k", type=parse_top_k, default=5)
    parser.add_argument("--min-hybrid-recall", type=float, default=0.80)
    parser.add_argument("--min-no-context-accuracy", type=float, default=1.00)
    parser.add_argument("--min-semantic-recall", type=float, default=0.80)
    parser.add_argument("--min-semantic-no-context", type=float, default=1.00)
    parser.add_argument("--min-hybrid-precision", type=float, default=0.20)
    parser.add_argument("--min-semantic-precision", type=float, default=0.20)
    parser.add_argument("--min-expected-binding-recall", type=float, default=0.95)
    parser.add_argument("--min-multi-binding-full-hit", type=float, default=0.60)
    parser.add_argument("--min-distractor-free-rate", type=float, default=1.00)
    parser.add_argument("--min-category-recall", type=float, default=0.0)
    parser.add_argument("--min-category-binding-recall", type=float, default=0.0)
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args()

    report = build_retrieval_eval_report(
        fixture_path=args.fixture,
        top_k=args.top_k,
        min_hybrid_recall=args.min_hybrid_recall,
        min_no_context_accuracy=args.min_no_context_accuracy,
        min_semantic_recall=args.min_semantic_recall,
        min_semantic_no_context=args.min_semantic_no_context,
        min_hybrid_precision=args.min_hybrid_precision,
        min_semantic_precision=args.min_semantic_precision,
        min_expected_binding_recall=args.min_expected_binding_recall,
        min_multi_binding_full_hit=args.min_multi_binding_full_hit,
        min_distractor_free_rate=args.min_distractor_free_rate,
        min_category_recall=args.min_category_recall,
        min_category_binding_recall=args.min_category_binding_recall,
    )
    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for line in format_retrieval_eval_text(report):
            print(line)
    return 0 if report["status"] == "RETRIEVAL_EVAL_OK" else 2


if __name__ == "__main__":
    raise SystemExit(main())
