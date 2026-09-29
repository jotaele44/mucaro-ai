from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.retrieval_scale_benchmark import (
    ScaleBenchmarkError,
    _threshold_failures,
    build_corpus,
    build_queries,
    evaluate_queries,
    load_denominator,
)
from backend.app.utils import tokenize


def test_rt10_denominator_is_frozen_and_arithmetic_closes() -> None:
    denominator = load_denominator()
    assert denominator["schema_version"] == "MUCARO_RT10_SCALE_BENCHMARK/1.1"
    assert denominator["corpus"]["scale_levels"] == [1000, 5000, 10000]
    assert denominator["corpus"]["max_chunks"] == 10000
    assert denominator["query_denominator"] == {
        "answerable": 24,
        "difficult": 16,
        "ambiguous": 16,
        "negative": 24,
        "total": 80,
    }
    assert denominator["thresholds"]["min_expected_source_hit_rate"] == 1.0
    assert denominator["thresholds"]["max_unsupported_source_claim_rate"] == 0.0


def test_rt10_generated_denominator_preserves_unique_ids_and_categories() -> None:
    denominator = load_denominator()
    chunks = build_corpus(1000)
    queries = build_queries(denominator)
    assert len(chunks) == 1000
    assert len({row["chunk_id"] for row in chunks}) == 1000
    assert len({row["source_id"] for row in chunks}) == 1000
    assert len(queries) == 80
    assert len({row["query_id"] for row in queries}) == 80
    categories = {
        name: sum(row["category"] == name for row in queries)
        for name in denominator["query_denominator"]
        if name != "total"
    }
    assert categories == {
        "answerable": 24,
        "difficult": 16,
        "ambiguous": 16,
        "negative": 24,
    }


def test_rt10_negative_queries_are_true_no_context() -> None:
    denominator = load_denominator()
    chunks = build_corpus(400)
    corpus_tokens: set[str] = set()
    for chunk in chunks:
        corpus_tokens.update(tokenize(str(chunk["text"])))
    negatives = [row for row in build_queries(denominator) if row["category"] == "negative"]
    assert len(negatives) == 24
    for case in negatives:
        assert set(tokenize(str(case["query"]))).isdisjoint(corpus_tokens)


def test_rt10_small_positive_negative_and_citation_controls_pass() -> None:
    denominator = load_denominator()
    queries = build_queries(denominator)
    selected = (
        [row for row in queries if row["category"] == "answerable"][:4]
        + [row for row in queries if row["category"] == "difficult"][:4]
        + [row for row in queries if row["category"] == "ambiguous"][:4]
        + [row for row in queries if row["category"] == "negative"][:4]
    )
    report = evaluate_queries(build_corpus(400), selected, strategy="hybrid", top_k=5)
    metrics = report["metrics"]
    assert metrics["expected_source_hit_rate"] == 1.0
    assert metrics["expected_binding_recall"] == 1.0
    assert metrics["ambiguous_full_hit_rate"] == 1.0
    assert metrics["negative_query_accuracy"] == 1.0
    assert metrics["citation_correctness"] == 1.0
    assert metrics["citation_completeness"] == 1.0
    assert metrics["faithfulness"] == 1.0
    assert metrics["unsupported_source_claim_rate"] == 0.0


def test_rt10_negative_control_detects_false_no_context() -> None:
    denominator = load_denominator()
    negative = next(row for row in build_queries(denominator) if row["query_id"] == "negative-00")
    chunks = build_corpus(400)
    chunks.append(
        {
            "chunk_id": "rt10_negative_injection",
            "source_id": "rt10_negative_injection",
            "chunk_index": 0,
            "source_format": "synthetic_txt",
            "text": "This deliberate falsification fixture contains absenttoken00 and must break no-context accuracy.",
        }
    )
    report = evaluate_queries(chunks, [negative], strategy="hybrid", top_k=5)
    assert report["metrics"]["negative_query_accuracy"] == 0.0


def test_rt10_denominator_drift_fails_closed(tmp_path: Path) -> None:
    denominator = load_denominator()
    mutated = json.loads(json.dumps(denominator))
    mutated["query_denominator"]["total"] = 81
    path = tmp_path / "mutated.json"
    path.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(ScaleBenchmarkError, match="ERROR_SCALE_QUERY_ARITHMETIC"):
        load_denominator(path)


def test_rt10_threshold_falsification_is_detected() -> None:
    denominator = load_denominator()
    metrics = {
        "expected_source_hit_rate": 0.99,
        "expected_binding_recall": 1.0,
        "ambiguous_full_hit_rate": 1.0,
        "negative_query_accuracy": 1.0,
        "citation_correctness": 1.0,
        "citation_completeness": 1.0,
        "faithfulness": 1.0,
        "unsupported_source_claim_rate": 0.0,
        "p95_latency_ms": 10.0,
        "max_latency_ms": 20.0,
        "throughput_qps": 10.0,
    }
    failures = _threshold_failures(
        denominator,
        metrics,
        [
            {"chunk_count": 1000, "metrics": {"p95_latency_ms": 10.0}},
            {"chunk_count": 10000, "metrics": {"p95_latency_ms": 20.0}},
        ],
        peak_rss_mib=100.0,
        wall_seconds=1.0,
    )
    assert "expected_source_hit_rate<min_expected_source_hit_rate" in failures
