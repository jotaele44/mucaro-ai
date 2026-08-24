#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "backend" / "tests" / "fixtures" / "retrieval_eval.json"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from backend.app.config import Settings  # noqa: E402
from backend.app.retrieval import retrieve  # noqa: E402
from backend.app.vector_index import build_persistent_index  # noqa: E402


def load_fixture(path: Path = FIXTURE) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def make_eval_settings(root: Path) -> Settings:
    return Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "eval.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )


def evaluate_strategy(strategy: str, fixture: dict[str, Any], *, top_k: int) -> dict[str, Any]:
    chunks = fixture["chunks"]
    cfg = None
    temp_dir = None
    if strategy == "persistent":
        temp_dir = tempfile.TemporaryDirectory(prefix="mucaro-retrieval-eval-")
        cfg = make_eval_settings(Path(temp_dir.name))
        build_persistent_index(chunks, cfg)
    rows = []
    hits = 0
    top1_hits = 0
    no_context_hits = 0
    expected_with_context = 0
    expected_no_context = 0
    for case in fixture["queries"]:
        expected = set(case["expected_chunk_ids"])
        result = retrieve(case["query"], chunks, top_k=top_k, strategy=strategy, cfg=cfg) if cfg else retrieve(
            case["query"],
            chunks,
            top_k=top_k,
            strategy=strategy,
        )
        retrieved_ids = [chunk["chunk_id"] for chunk in result["retrieved_chunks"]]
        if expected:
            expected_with_context += 1
            hit = bool(expected.intersection(retrieved_ids))
            top1_hit = bool(retrieved_ids and retrieved_ids[0] in expected)
            hits += int(hit)
            top1_hits += int(top1_hit)
        else:
            expected_no_context += 1
            no_context_hits += int(result["status"] == "ERROR_NO_RETRIEVAL_CONTEXT")
            hit = result["status"] == "ERROR_NO_RETRIEVAL_CONTEXT"
            top1_hit = False
        rows.append(
            {
                "query": case["query"],
                "expected_chunk_ids": sorted(expected),
                "retrieved_chunk_ids": retrieved_ids,
                "status": result["status"],
                "hit": hit,
                "top1_hit": top1_hit,
            }
        )
    if temp_dir is not None:
        temp_dir.cleanup()
    return {
        "strategy": strategy,
        "recall_at_k": round(hits / expected_with_context, 4) if expected_with_context else 0.0,
        "top1_hit_rate": round(top1_hits / expected_with_context, 4) if expected_with_context else 0.0,
        "no_context_accuracy": round(no_context_hits / expected_no_context, 4) if expected_no_context else 1.0,
        "cases": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate deterministic retrieval strategies against the committed fixture.")
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--min-hybrid-recall", type=float, default=0.80)
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args()

    fixture = load_fixture(args.fixture)
    report = {
        "fixture": str(args.fixture.relative_to(ROOT) if args.fixture.is_relative_to(ROOT) else args.fixture),
        "top_k": args.top_k,
        "strategies": [
            evaluate_strategy(strategy, fixture, top_k=args.top_k)
            for strategy in ["lexical", "vector", "hybrid", "persistent"]
        ],
    }
    hybrid = next(item for item in report["strategies"] if item["strategy"] == "hybrid")
    passed = hybrid["recall_at_k"] >= args.min_hybrid_recall
    report["status"] = "RETRIEVAL_EVAL_OK" if passed else "RETRIEVAL_EVAL_FAILED"
    report["min_hybrid_recall"] = args.min_hybrid_recall

    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for strategy in report["strategies"]:
            print(
                f"{strategy['strategy']} recall@{args.top_k}={strategy['recall_at_k']:.2f} "
                f"top1={strategy['top1_hit_rate']:.2f} no_context={strategy['no_context_accuracy']:.2f}"
            )
        print(report["status"])
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
