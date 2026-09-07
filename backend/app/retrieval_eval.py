from __future__ import annotations

import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from .config import Settings
from .retrieval import retrieve
from .semantic_store import build_semantic_store
from .vector_index import build_persistent_index


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE = ROOT / "backend" / "tests" / "fixtures" / "retrieval_eval.json"
DEFAULT_STRATEGIES = ["lexical", "vector", "bm25", "hybrid", "persistent", "semantic"]
MIN_FIXTURE_CHUNKS = 24
MIN_FIXTURE_QUERIES = 30
MIN_POSITIVE_QUERIES = 24
MIN_NO_CONTEXT_QUERIES = 6
MIN_SOURCE_IDS = 20
MIN_MULTI_BINDING_QUERIES = 2
MIN_QUERY_CATEGORIES = 8
UNCATEGORIZED_QUERY_CATEGORY = "uncategorized"


def load_retrieval_eval_fixture(path: Path = DEFAULT_FIXTURE) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _eval_check(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def validate_retrieval_eval_fixture(fixture: Any) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    if not isinstance(fixture, dict):
        return {
            "status": "RETRIEVAL_FIXTURE_INVALID",
            "checks": [
                _eval_check(
                    "fixture_shape",
                    "FAIL",
                    f"fixture_type={type(fixture).__name__} chunks_type=missing queries_type=missing",
                )
            ],
            "counts": {
                "chunks": 0,
                "queries": 0,
                "positive_queries": 0,
                "no_context_queries": 0,
                "expected_bindings": 0,
                "distractor_bindings": 0,
                "distractor_queries": 0,
                "source_ids": 0,
                "source_formats": 0,
                "multi_binding_queries": 0,
                "query_categories": 0,
            },
        }
    chunks = fixture.get("chunks")
    queries = fixture.get("queries")
    chunk_rows = chunks if isinstance(chunks, list) else []
    query_rows = queries if isinstance(queries, list) else []

    checks.append(
        _eval_check(
            "fixture_shape",
            "PASS" if isinstance(chunks, list) and isinstance(queries, list) else "FAIL",
            f"chunks_type={type(chunks).__name__} queries_type={type(queries).__name__}",
        )
    )
    checks.append(
        _eval_check(
            "fixture_size",
            "PASS" if len(chunk_rows) >= MIN_FIXTURE_CHUNKS and len(query_rows) >= MIN_FIXTURE_QUERIES else "FAIL",
            f"chunks={len(chunk_rows)} min_chunks={MIN_FIXTURE_CHUNKS} queries={len(query_rows)} min_queries={MIN_FIXTURE_QUERIES}",
        )
    )

    chunk_ids: list[str] = []
    malformed_chunks = 0
    malformed_source_formats = 0
    for index, chunk in enumerate(chunk_rows):
        if not isinstance(chunk, dict):
            malformed_chunks += 1
            continue
        chunk_id = chunk.get("chunk_id")
        source_id = chunk.get("source_id")
        text = chunk.get("text")
        if not isinstance(chunk_id, str) or not chunk_id.strip():
            malformed_chunks += 1
            continue
        chunk_ids.append(chunk_id)
        source_format = chunk.get("source_format")
        if source_format is not None and (not isinstance(source_format, str) or not source_format.strip()):
            malformed_source_formats += 1
        if not isinstance(source_id, str) or not source_id.strip() or not isinstance(text, str) or not text.strip():
            malformed_chunks += 1
        if "chunk_index" not in chunk:
            malformed_chunks += 1
    chunk_id_counts = Counter(chunk_ids)
    duplicate_chunk_ids = sorted(chunk_id for chunk_id, count in chunk_id_counts.items() if count > 1)
    known_chunk_ids = set(chunk_ids)
    source_ids = {chunk.get("source_id") for chunk in chunk_rows if isinstance(chunk, dict) and isinstance(chunk.get("source_id"), str)}
    source_formats = {
        chunk.get("source_format")
        for chunk in chunk_rows
        if isinstance(chunk, dict) and isinstance(chunk.get("source_format"), str) and chunk.get("source_format").strip()
    }
    checks.append(
        _eval_check(
            "chunk_contract",
            "PASS" if not malformed_chunks and not duplicate_chunk_ids else "FAIL",
            f"malformed={malformed_chunks} duplicates={','.join(duplicate_chunk_ids) or 'none'}",
        )
    )
    checks.append(
        _eval_check(
            "source_diversity",
            "PASS" if len(source_ids) >= MIN_SOURCE_IDS else "FAIL",
            f"source_ids={len(source_ids)} min_source_ids={MIN_SOURCE_IDS}",
        )
    )
    checks.append(
        _eval_check(
            "source_format_contract",
            "PASS" if malformed_source_formats == 0 else "FAIL",
            f"source_formats={len(source_formats)} malformed={malformed_source_formats}",
        )
    )

    malformed_queries = 0
    duplicate_queries: set[str] = set()
    seen_queries: set[str] = set()
    dangling_expected: set[str] = set()
    dangling_distractors: set[str] = set()
    overlapping_expected_and_distractors: set[str] = set()
    query_categories: set[str] = set()
    positive_queries = 0
    no_context_queries = 0
    multi_binding_queries = 0
    expected_bindings = 0
    distractor_bindings = 0
    distractor_queries = 0
    for row in query_rows:
        if not isinstance(row, dict):
            malformed_queries += 1
            continue
        query = row.get("query")
        expected = row.get("expected_chunk_ids")
        if not isinstance(query, str) or not query.strip() or not isinstance(expected, list):
            malformed_queries += 1
            continue
        distractors = row.get("distractor_chunk_ids", [])
        if not isinstance(distractors, list):
            malformed_queries += 1
            distractors = []
        if query in seen_queries:
            duplicate_queries.add(query)
        seen_queries.add(query)
        if expected:
            positive_queries += 1
            if len(expected) > 1:
                multi_binding_queries += 1
        else:
            no_context_queries += 1
        category = row.get("category")
        if isinstance(category, str) and category.strip():
            query_categories.add(category)
        expected_set = set()
        for chunk_id in expected:
            if not isinstance(chunk_id, str) or not chunk_id.strip():
                malformed_queries += 1
                continue
            expected_set.add(chunk_id)
            expected_bindings += 1
            if chunk_id not in known_chunk_ids:
                dangling_expected.add(chunk_id)
        if distractors:
            distractor_queries += 1
        for chunk_id in distractors:
            if not isinstance(chunk_id, str) or not chunk_id.strip():
                malformed_queries += 1
                continue
            distractor_bindings += 1
            if chunk_id not in known_chunk_ids:
                dangling_distractors.add(chunk_id)
            if chunk_id in expected_set:
                overlapping_expected_and_distractors.add(chunk_id)
    checks.append(
        _eval_check(
            "query_contract",
            "PASS" if not malformed_queries and not duplicate_queries else "FAIL",
            f"malformed={malformed_queries} duplicate_queries={len(duplicate_queries)}",
        )
    )
    checks.append(
        _eval_check(
            "expected_chunk_bindings",
            "PASS" if not dangling_expected else "FAIL",
            f"dangling={','.join(sorted(dangling_expected)) or 'none'} expected_bindings={expected_bindings}",
        )
    )
    checks.append(
        _eval_check(
            "distractor_chunk_bindings",
            "PASS" if not dangling_distractors and not overlapping_expected_and_distractors else "FAIL",
            (
                f"dangling={','.join(sorted(dangling_distractors)) or 'none'} "
                f"overlap={','.join(sorted(overlapping_expected_and_distractors)) or 'none'} "
                f"distractor_bindings={distractor_bindings}"
            ),
        )
    )
    checks.append(
        _eval_check(
            "negative_cases",
            "PASS" if positive_queries >= MIN_POSITIVE_QUERIES and no_context_queries >= MIN_NO_CONTEXT_QUERIES else "FAIL",
            (
                f"positive={positive_queries} min_positive={MIN_POSITIVE_QUERIES} "
                f"no_context={no_context_queries} min_no_context={MIN_NO_CONTEXT_QUERIES}"
            ),
        )
    )
    checks.append(
        _eval_check(
            "multi_binding_cases",
            "PASS" if multi_binding_queries >= MIN_MULTI_BINDING_QUERIES else "FAIL",
            f"multi_binding={multi_binding_queries} min_multi_binding={MIN_MULTI_BINDING_QUERIES}",
        )
    )
    checks.append(
        _eval_check(
            "query_category_coverage",
            "PASS" if len(query_categories) >= MIN_QUERY_CATEGORIES else "FAIL",
            f"categories={len(query_categories)} min_categories={MIN_QUERY_CATEGORIES}",
        )
    )

    failed = [check for check in checks if check["status"] == "FAIL"]
    return {
        "status": "RETRIEVAL_FIXTURE_INVALID" if failed else "RETRIEVAL_FIXTURE_OK",
        "checks": checks,
        "counts": {
            "chunks": len(chunk_rows),
            "queries": len(query_rows),
            "positive_queries": positive_queries,
            "no_context_queries": no_context_queries,
            "expected_bindings": expected_bindings,
            "distractor_bindings": distractor_bindings,
            "distractor_queries": distractor_queries,
            "source_ids": len(source_ids),
            "source_formats": len(source_formats),
            "multi_binding_queries": multi_binding_queries,
            "query_categories": len(query_categories),
        },
    }


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


def _empty_category_bucket(category: str) -> dict[str, Any]:
    return {
        "category": category,
        "queries": 0,
        "positive_queries": 0,
        "no_context_queries": 0,
        "hits": 0,
        "top1_hits": 0,
        "no_context_hits": 0,
        "full_binding_hits": 0,
        "multi_binding_cases": 0,
        "multi_binding_full_hits": 0,
        "distractor_queries": 0,
        "distractor_top1_hits": 0,
        "total_distractor_bindings": 0,
        "distractor_hits": 0,
        "total_expected_bindings": 0,
        "retrieved_for_positive": 0,
        "relevant_retrieved": 0,
    }


def _category_from_case(case: dict[str, Any]) -> str:
    category = case.get("category")
    return category.strip() if isinstance(category, str) and category.strip() else UNCATEGORIZED_QUERY_CATEGORY


def _finalize_category_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    positive_queries = bucket["positive_queries"]
    no_context_queries = bucket["no_context_queries"]
    multi_binding_cases = bucket["multi_binding_cases"]
    distractor_queries = bucket["distractor_queries"]
    return {
        "category": bucket["category"],
        "queries": bucket["queries"],
        "positive_queries": positive_queries,
        "no_context_queries": no_context_queries,
        "recall_at_k": round(bucket["hits"] / positive_queries, 4) if positive_queries else 1.0,
        "expected_binding_recall_at_k": (
            round(bucket["relevant_retrieved"] / bucket["total_expected_bindings"], 4)
            if bucket["total_expected_bindings"]
            else 1.0
        ),
        "context_precision_at_k": (
            round(bucket["relevant_retrieved"] / bucket["retrieved_for_positive"], 4)
            if bucket["retrieved_for_positive"]
            else 1.0
        ),
        "top1_hit_rate": round(bucket["top1_hits"] / positive_queries, 4) if positive_queries else 1.0,
        "full_binding_hit_rate": round(bucket["full_binding_hits"] / positive_queries, 4) if positive_queries else 1.0,
        "multi_binding_full_hit_rate": (
            round(bucket["multi_binding_full_hits"] / multi_binding_cases, 4) if multi_binding_cases else 1.0
        ),
        "distractor_free_rate": (
            round(1 - (bucket["distractor_top1_hits"] / distractor_queries), 4)
            if distractor_queries
            else 1.0
        ),
        "distractor_hit_rate": (
            round(bucket["distractor_hits"] / bucket["total_distractor_bindings"], 4)
            if bucket["total_distractor_bindings"]
            else 0.0
        ),
        "no_context_accuracy": round(bucket["no_context_hits"] / no_context_queries, 4) if no_context_queries else 1.0,
        "expected_bindings": bucket["total_expected_bindings"],
        "distractor_bindings": bucket["total_distractor_bindings"],
        "distractor_hits": bucket["distractor_hits"],
        "distractor_top1_hits": bucket["distractor_top1_hits"],
        "positive_retrieved_chunks": bucket["retrieved_for_positive"],
        "relevant_retrieved_chunks": bucket["relevant_retrieved"],
    }


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
    total_expected_bindings = 0
    retrieved_for_positive = 0
    relevant_retrieved = 0
    full_binding_hits = 0
    multi_binding_cases = 0
    multi_binding_full_hits = 0
    distractor_queries = 0
    distractor_top1_hits = 0
    total_distractor_bindings = 0
    distractor_hits = 0
    category_buckets: dict[str, dict[str, Any]] = {}
    try:
        for case in fixture["queries"]:
            expected = set(case["expected_chunk_ids"])
            distractors = set(case.get("distractor_chunk_ids", []))
            category = _category_from_case(case)
            category_bucket = category_buckets.setdefault(category, _empty_category_bucket(category))
            category_bucket["queries"] += 1
            result = (
                retrieve(case["query"], chunks, top_k=top_k, strategy=strategy, cfg=cfg)
                if cfg
                else retrieve(case["query"], chunks, top_k=top_k, strategy=strategy)
            )
            retrieved_ids = [chunk["chunk_id"] for chunk in result["retrieved_chunks"]]
            retrieved = set(retrieved_ids)
            matched_expected = sorted(expected.intersection(retrieved))
            matched_distractors = sorted(distractors.intersection(retrieved))
            distractor_top1_hit = bool(retrieved_ids and retrieved_ids[0] in distractors)
            if distractors:
                distractor_queries += 1
                total_distractor_bindings += len(distractors)
                distractor_hits += len(matched_distractors)
                distractor_top1_hits += int(distractor_top1_hit)
                category_bucket["distractor_queries"] += 1
                category_bucket["total_distractor_bindings"] += len(distractors)
                category_bucket["distractor_hits"] += len(matched_distractors)
                category_bucket["distractor_top1_hits"] += int(distractor_top1_hit)
            if expected:
                expected_with_context += 1
                total_expected_bindings += len(expected)
                retrieved_for_positive += len(retrieved_ids)
                relevant_retrieved += len(matched_expected)
                category_bucket["positive_queries"] += 1
                category_bucket["total_expected_bindings"] += len(expected)
                category_bucket["retrieved_for_positive"] += len(retrieved_ids)
                category_bucket["relevant_retrieved"] += len(matched_expected)
                hit = bool(expected.intersection(retrieved_ids))
                top1_hit = bool(retrieved_ids and retrieved_ids[0] in expected)
                full_binding_hit = expected.issubset(retrieved)
                full_binding_hits += int(full_binding_hit)
                category_bucket["hits"] += int(hit)
                category_bucket["top1_hits"] += int(top1_hit)
                category_bucket["full_binding_hits"] += int(full_binding_hit)
                if len(expected) > 1:
                    multi_binding_cases += 1
                    multi_binding_full_hits += int(full_binding_hit)
                    category_bucket["multi_binding_cases"] += 1
                    category_bucket["multi_binding_full_hits"] += int(full_binding_hit)
                hits += int(hit)
                top1_hits += int(top1_hit)
            else:
                expected_no_context += 1
                no_context_hits += int(result["status"] == "ERROR_NO_RETRIEVAL_CONTEXT")
                category_bucket["no_context_queries"] += 1
                category_bucket["no_context_hits"] += int(result["status"] == "ERROR_NO_RETRIEVAL_CONTEXT")
                hit = result["status"] == "ERROR_NO_RETRIEVAL_CONTEXT"
                top1_hit = False
                full_binding_hit = False
            rows.append(
                {
                    "case_type": "positive" if expected else "no_context",
                    "category": category,
                    "query": case["query"],
                    "expected_chunk_ids": sorted(expected),
                    "distractor_chunk_ids": sorted(distractors),
                    "retrieved_chunk_ids": retrieved_ids,
                    "matched_expected_chunk_ids": matched_expected,
                    "matched_distractor_chunk_ids": matched_distractors,
                    "distractor_top1_hit": distractor_top1_hit,
                    "status": result["status"],
                    "hit": hit,
                    "top1_hit": top1_hit,
                    "full_binding_hit": full_binding_hit,
                }
            )
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()

    return {
        "strategy": strategy,
        "recall_at_k": round(hits / expected_with_context, 4) if expected_with_context else 0.0,
        "expected_binding_recall_at_k": round(relevant_retrieved / total_expected_bindings, 4) if total_expected_bindings else 0.0,
        "context_precision_at_k": round(relevant_retrieved / retrieved_for_positive, 4) if retrieved_for_positive else 0.0,
        "top1_hit_rate": round(top1_hits / expected_with_context, 4) if expected_with_context else 0.0,
        "full_binding_hit_rate": round(full_binding_hits / expected_with_context, 4) if expected_with_context else 0.0,
        "multi_binding_full_hit_rate": round(multi_binding_full_hits / multi_binding_cases, 4) if multi_binding_cases else 1.0,
        "distractor_free_rate": round(1 - (distractor_top1_hits / distractor_queries), 4) if distractor_queries else 1.0,
        "distractor_hit_rate": round(distractor_hits / total_distractor_bindings, 4) if total_distractor_bindings else 0.0,
        "no_context_accuracy": round(no_context_hits / expected_no_context, 4) if expected_no_context else 1.0,
        "positive_retrieved_chunks": retrieved_for_positive,
        "relevant_retrieved_chunks": relevant_retrieved,
        "expected_bindings": total_expected_bindings,
        "distractor_queries": distractor_queries,
        "distractor_bindings": total_distractor_bindings,
        "distractor_hits": distractor_hits,
        "distractor_top1_hits": distractor_top1_hits,
        "category_metrics": [
            _finalize_category_bucket(category_buckets[category])
            for category in sorted(category_buckets)
        ],
        "cases": rows,
    }


def build_retrieval_eval_report(
    *,
    fixture_path: Path = DEFAULT_FIXTURE,
    top_k: int = 5,
    min_hybrid_recall: float = 0.80,
    min_no_context_accuracy: float = 1.00,
    min_semantic_recall: float = 0.80,
    min_semantic_no_context: float = 1.00,
    min_hybrid_precision: float = 0.20,
    min_semantic_precision: float = 0.20,
    min_expected_binding_recall: float = 0.95,
    min_multi_binding_full_hit: float = 0.60,
    min_distractor_free_rate: float = 1.00,
    min_category_recall: float = 0.0,
    min_category_binding_recall: float = 0.0,
    min_source_formats: int = 0,
) -> dict[str, Any]:
    if min_source_formats < 0:
        raise ValueError("MIN_SOURCE_FORMATS_MUST_BE_NON_NEGATIVE")
    fixture = load_retrieval_eval_fixture(fixture_path)
    fixture_validation = validate_retrieval_eval_fixture(fixture)
    if fixture_validation["status"] != "RETRIEVAL_FIXTURE_OK":
        return {
            "status": "RETRIEVAL_EVAL_INVALID",
            "fixture": str(fixture_path.relative_to(ROOT) if fixture_path.is_relative_to(ROOT) else fixture_path),
            "top_k": top_k,
            "strategies": [],
            "fixture_validation": fixture_validation,
            "counts": fixture_validation["counts"] | {"strategies": 0},
            "min_hybrid_recall": min_hybrid_recall,
            "min_no_context_accuracy": min_no_context_accuracy,
            "min_semantic_recall": min_semantic_recall,
            "min_semantic_no_context": min_semantic_no_context,
            "min_hybrid_precision": min_hybrid_precision,
            "min_semantic_precision": min_semantic_precision,
            "min_expected_binding_recall": min_expected_binding_recall,
            "min_multi_binding_full_hit": min_multi_binding_full_hit,
            "min_distractor_free_rate": min_distractor_free_rate,
            "min_category_recall": min_category_recall,
            "min_category_binding_recall": min_category_binding_recall,
            "min_source_formats": min_source_formats,
            "category_threshold_failures": [],
            "distractor_threshold_failures": [],
            "source_format_threshold_failures": [],
        }
    report: dict[str, Any] = {
        "fixture": str(fixture_path.relative_to(ROOT) if fixture_path.is_relative_to(ROOT) else fixture_path),
        "top_k": top_k,
        "strategies": [
            evaluate_retrieval_strategy(strategy, fixture, top_k=top_k)
            for strategy in DEFAULT_STRATEGIES
        ],
        "min_hybrid_recall": min_hybrid_recall,
        "min_no_context_accuracy": min_no_context_accuracy,
        "min_semantic_recall": min_semantic_recall,
        "min_semantic_no_context": min_semantic_no_context,
        "min_hybrid_precision": min_hybrid_precision,
        "min_semantic_precision": min_semantic_precision,
        "min_expected_binding_recall": min_expected_binding_recall,
        "min_multi_binding_full_hit": min_multi_binding_full_hit,
        "min_distractor_free_rate": min_distractor_free_rate,
        "min_category_recall": min_category_recall,
        "min_category_binding_recall": min_category_binding_recall,
        "min_source_formats": min_source_formats,
        "fixture_validation": fixture_validation,
    }
    by_strategy = {item["strategy"]: item for item in report["strategies"]}
    hybrid = by_strategy["hybrid"]
    semantic = by_strategy["semantic"]
    category_threshold_failures = [
        {
            "strategy": strategy["strategy"],
            "category": category["category"],
            "recall_at_k": category["recall_at_k"],
            "expected_binding_recall_at_k": category["expected_binding_recall_at_k"],
        }
        for strategy in report["strategies"]
        for category in strategy.get("category_metrics", [])
        if category["positive_queries"] > 0
        and (
            category["recall_at_k"] < min_category_recall
            or category["expected_binding_recall_at_k"] < min_category_binding_recall
        )
    ]
    distractor_threshold_failures = [
        {
            "strategy": strategy["strategy"],
            "distractor_free_rate": strategy["distractor_free_rate"],
            "distractor_hit_rate": strategy["distractor_hit_rate"],
            "distractor_hits": strategy["distractor_hits"],
        }
        for strategy in report["strategies"]
        if strategy["distractor_queries"] > 0 and strategy["distractor_free_rate"] < min_distractor_free_rate
    ]
    source_format_threshold_failures = []
    source_format_count = fixture_validation["counts"]["source_formats"]
    if source_format_count < min_source_formats:
        source_format_threshold_failures.append(
            {
                "source_formats": source_format_count,
                "min_source_formats": min_source_formats,
            }
        )
    passed = (
        hybrid["recall_at_k"] >= min_hybrid_recall
        and hybrid["context_precision_at_k"] >= min_hybrid_precision
        and all(strategy["no_context_accuracy"] >= min_no_context_accuracy for strategy in report["strategies"])
        and all(strategy["expected_binding_recall_at_k"] >= min_expected_binding_recall for strategy in report["strategies"])
        and all(strategy["multi_binding_full_hit_rate"] >= min_multi_binding_full_hit for strategy in report["strategies"])
        and semantic["recall_at_k"] >= min_semantic_recall
        and semantic["context_precision_at_k"] >= min_semantic_precision
        and semantic["no_context_accuracy"] >= min_semantic_no_context
        and not category_threshold_failures
        and not distractor_threshold_failures
        and not source_format_threshold_failures
    )
    report["status"] = "RETRIEVAL_EVAL_OK" if passed else "RETRIEVAL_EVAL_FAILED"
    report["category_threshold_failures"] = category_threshold_failures
    report["distractor_threshold_failures"] = distractor_threshold_failures
    report["source_format_threshold_failures"] = source_format_threshold_failures
    report["counts"] = {
        "chunks": len(fixture.get("chunks", [])),
        "queries": len(fixture.get("queries", [])),
        "positive_queries": fixture_validation["counts"]["positive_queries"],
        "no_context_queries": fixture_validation["counts"]["no_context_queries"],
        "expected_bindings": fixture_validation["counts"]["expected_bindings"],
        "distractor_bindings": fixture_validation["counts"]["distractor_bindings"],
        "distractor_queries": fixture_validation["counts"]["distractor_queries"],
        "source_ids": fixture_validation["counts"]["source_ids"],
        "source_formats": fixture_validation["counts"]["source_formats"],
        "multi_binding_queries": fixture_validation["counts"]["multi_binding_queries"],
        "query_categories": fixture_validation["counts"]["query_categories"],
        "strategies": len(report["strategies"]),
    }
    return report


def format_retrieval_eval_text(report: dict[str, Any]) -> list[str]:
    lines = []
    top_k = report.get("top_k")
    for strategy in report.get("strategies", []):
        lines.append(
            f"{strategy['strategy']} recall@{top_k}={strategy['recall_at_k']:.2f} "
            f"binding_recall={strategy['expected_binding_recall_at_k']:.2f} "
            f"precision={strategy['context_precision_at_k']:.2f} top1={strategy['top1_hit_rate']:.2f} "
            f"full_binding={strategy['full_binding_hit_rate']:.2f} "
            f"multi_full={strategy['multi_binding_full_hit_rate']:.2f} "
            f"distractor_free={strategy['distractor_free_rate']:.2f} "
            f"no_context={strategy['no_context_accuracy']:.2f}"
        )
        weakest_categories = sorted(
            strategy.get("category_metrics", []),
            key=lambda row: (
                row["expected_binding_recall_at_k"],
                row["recall_at_k"],
                row["context_precision_at_k"],
                row["category"],
            ),
        )[:3]
        for category in weakest_categories:
            lines.append(
                f"{strategy['strategy']} category={category['category']} "
                f"queries={category['queries']} positives={category['positive_queries']} "
                f"recall@{top_k}={category['recall_at_k']:.2f} "
                f"binding_recall={category['expected_binding_recall_at_k']:.2f} "
                f"precision={category['context_precision_at_k']:.2f} "
                f"distractor_free={category['distractor_free_rate']:.2f} "
                f"no_context={category['no_context_accuracy']:.2f}"
            )
    for failure in report.get("category_threshold_failures", []):
        if isinstance(failure, dict):
            lines.append(
                f"category_threshold_failure strategy={failure.get('strategy')} "
                f"category={failure.get('category')} recall={failure.get('recall_at_k')} "
                f"binding_recall={failure.get('expected_binding_recall_at_k')}"
            )
    for failure in report.get("distractor_threshold_failures", []):
        if isinstance(failure, dict):
            lines.append(
                f"distractor_threshold_failure strategy={failure.get('strategy')} "
                f"distractor_free={failure.get('distractor_free_rate')} "
                f"distractor_hits={failure.get('distractor_hits')}"
            )
    for failure in report.get("source_format_threshold_failures", []):
        if isinstance(failure, dict):
            lines.append(
                f"source_format_threshold_failure "
                f"source_formats={failure.get('source_formats')} "
                f"min_source_formats={failure.get('min_source_formats')}"
            )
    lines.append(str(report.get("status")))
    return lines
