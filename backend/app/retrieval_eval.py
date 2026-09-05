from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from .config import Settings
from .retrieval import retrieve
from .semantic_store import build_semantic_store
from .vector_index import build_persistent_index


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE = ROOT / "backend" / "tests" / "fixtures" / "retrieval_eval.json"
DEFAULT_STRATEGIES = ["lexical", "vector", "bm25", "hybrid", "persistent", "semantic"]


def load_retrieval_eval_fixture(path: Path = DEFAULT_FIXTURE) -> dict[str, Any]:
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


def evaluate_retrieval_strategy(strategy: str, fixture: dict[str, Any], *, top_k: int) -> dict[str, Any]:
    chunks = fixture["chunks"]
    cfg = None
    temp_dir = None
    if strategy in {"persistent", "semantic"}:
        temp_dir = tempfile.TemporaryDirectory(prefix="mucaro-retrieval-eval-")
        cfg = make_eval_settings(Path(temp_dir.name))
        if strategy == "persistent":
            build_persistent_index(chunks, cfg)
        else:
            build_semantic_store(chunks, cfg)

    rows = []
    hits = 0
    top1_hits = 0
    no_context_hits = 0
    expected_with_context = 0
    expected_no_context = 0
    try:
        for case in fixture["queries"]:
            expected = set(case["expected_chunk_ids"])
            result = (
                retrieve(case["query"], chunks, top_k=top_k, strategy=strategy, cfg=cfg)
                if cfg
                else retrieve(case["query"], chunks, top_k=top_k, strategy=strategy)
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
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()

    return {
        "strategy": strategy,
        "recall_at_k": round(hits / expected_with_context, 4) if expected_with_context else 0.0,
        "top1_hit_rate": round(top1_hits / expected_with_context, 4) if expected_with_context else 0.0,
        "no_context_accuracy": round(no_context_hits / expected_no_context, 4) if expected_no_context else 1.0,
        "cases": rows,
    }


def build_retrieval_eval_report(
    *,
    fixture_path: Path = DEFAULT_FIXTURE,
    top_k: int = 5,
    min_hybrid_recall: float = 0.80,
    min_semantic_recall: float = 0.80,
    min_semantic_no_context: float = 1.00,
) -> dict[str, Any]:
    fixture = load_retrieval_eval_fixture(fixture_path)
    report: dict[str, Any] = {
        "fixture": str(fixture_path.relative_to(ROOT) if fixture_path.is_relative_to(ROOT) else fixture_path),
        "top_k": top_k,
        "strategies": [
            evaluate_retrieval_strategy(strategy, fixture, top_k=top_k)
            for strategy in DEFAULT_STRATEGIES
        ],
        "min_hybrid_recall": min_hybrid_recall,
        "min_semantic_recall": min_semantic_recall,
        "min_semantic_no_context": min_semantic_no_context,
    }
    by_strategy = {item["strategy"]: item for item in report["strategies"]}
    hybrid = by_strategy["hybrid"]
    semantic = by_strategy["semantic"]
    passed = (
        hybrid["recall_at_k"] >= min_hybrid_recall
        and semantic["recall_at_k"] >= min_semantic_recall
        and semantic["no_context_accuracy"] >= min_semantic_no_context
    )
    report["status"] = "RETRIEVAL_EVAL_OK" if passed else "RETRIEVAL_EVAL_FAILED"
    report["counts"] = {
        "chunks": len(fixture.get("chunks", [])),
        "queries": len(fixture.get("queries", [])),
        "strategies": len(report["strategies"]),
    }
    return report


def format_retrieval_eval_text(report: dict[str, Any]) -> list[str]:
    lines = []
    top_k = report.get("top_k")
    for strategy in report.get("strategies", []):
        lines.append(
            f"{strategy['strategy']} recall@{top_k}={strategy['recall_at_k']:.2f} "
            f"top1={strategy['top1_hit_rate']:.2f} no_context={strategy['no_context_accuracy']:.2f}"
        )
    lines.append(str(report.get("status")))
    return lines
