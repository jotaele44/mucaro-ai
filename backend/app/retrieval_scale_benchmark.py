from __future__ import annotations

import hashlib
import json
import math
import platform
import resource
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .citations import build_citation_records, validate_citations
from .model_router import generate_answer
from .retrieval import retrieve

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DENOMINATOR = ROOT / "_architecture" / "retrieval_scale_benchmark_v1_2.json"
STATUS_OK = "RT10_SCALE_CERT_PASS"
STATUS_FAILED = "RT10_SCALE_CERT_FAIL"


class ScaleBenchmarkError(RuntimeError):
    pass


def stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def current_git_head(root: Path = ROOT) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else "UNKNOWN_HEAD"


def load_denominator(path: Path = DEFAULT_DENOMINATOR) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "MUCARO_RT10_SCALE_BENCHMARK/1.2":
        raise ScaleBenchmarkError("ERROR_SCALE_SCHEMA_VERSION")
    query_denominator = payload.get("query_denominator")
    if not isinstance(query_denominator, dict):
        raise ScaleBenchmarkError("ERROR_SCALE_QUERY_DENOMINATOR")
    expected_total = sum(
        int(query_denominator.get(key, -1))
        for key in ("answerable", "difficult", "ambiguous", "negative")
    )
    if expected_total != int(query_denominator.get("total", -2)):
        raise ScaleBenchmarkError("ERROR_SCALE_QUERY_ARITHMETIC")
    levels = payload.get("corpus", {}).get("scale_levels")
    if levels != [1000, 5000, 10000]:
        raise ScaleBenchmarkError("ERROR_SCALE_LEVEL_DRIFT")
    if int(payload.get("corpus", {}).get("max_chunks", 0)) != 10000:
        raise ScaleBenchmarkError("ERROR_SCALE_MAX_CHUNKS_DRIFT")
    thresholds = payload.get("thresholds")
    if not isinstance(thresholds, dict) or not thresholds:
        raise ScaleBenchmarkError("ERROR_SCALE_THRESHOLDS_MISSING")
    return payload


def _chunk_text(index: int) -> str:
    ambiguous_group = index // 2 if index < 32 else -1
    ambiguous_terms = f" ambig{ambiguous_group:02d} pairtoken{ambiguous_group:02d}" if ambiguous_group >= 0 else ""

    difficult_terms = ""
    if 200 <= index < 216:
        offset = index - 200
        difficult_terms = f" cluea{offset:02d} clueb{offset:02d}"
    elif 400 <= index < 416:
        difficult_terms = f" cluea{index - 400:02d}"
    elif 500 <= index < 516:
        difficult_terms = f" clueb{index - 500:02d}"

    return (
        f"Benchmark record {index:05d}. "
        f"Stable retrieval identity zxq{index:05d}. "
        f"Group grp{index % 100:03d}. "
        "This deterministic local corpus measures bounded retrieval latency, binding recall, and citation traceability."
        f"{ambiguous_terms}{difficult_terms}"
    )


def build_corpus(chunk_count: int) -> list[dict[str, Any]]:
    if chunk_count < 400:
        raise ScaleBenchmarkError("ERROR_SCALE_CORPUS_TOO_SMALL")
    return [
        {
            "chunk_id": f"rt10_chunk_{index:05d}",
            "source_id": f"rt10_source_{index:05d}",
            "chunk_index": 0,
            "source_format": "synthetic_txt",
            "text": _chunk_text(index),
        }
        for index in range(chunk_count)
    ]


def build_queries(denominator: dict[str, Any]) -> list[dict[str, Any]]:
    counts = denominator["query_denominator"]
    rows: list[dict[str, Any]] = []

    for offset in range(int(counts["answerable"])):
        index = 100 + offset
        rows.append(
            {
                "query_id": f"answerable-{offset:02d}",
                "category": "answerable",
                "query": f"What record contains stable retrieval identity zxq{index:05d}?",
                "expected_source_ids": [f"rt10_source_{index:05d}"],
            }
        )

    for offset in range(int(counts["difficult"])):
        index = 200 + offset
        rows.append(
            {
                "query_id": f"difficult-{offset:02d}",
                "category": "difficult",
                "query": (
                    "Which benchmark record jointly binds "
                    f"cluea{offset:02d} and clueb{offset:02d} under bounded citation traceability?"
                ),
                "expected_source_ids": [f"rt10_source_{index:05d}"],
            }
        )

    for offset in range(int(counts["ambiguous"])):
        left = offset * 2
        right = left + 1
        rows.append(
            {
                "query_id": f"ambiguous-{offset:02d}",
                "category": "ambiguous",
                "query": f"Which records share ambig{offset:02d} pairtoken{offset:02d}?",
                "expected_source_ids": [
                    f"rt10_source_{left:05d}",
                    f"rt10_source_{right:05d}",
                ],
            }
        )

    for offset in range(int(counts["negative"])):
        rows.append(
            {
                "query_id": f"negative-{offset:02d}",
                "category": "negative",
                "query": f"absenttoken{offset:02d} voidneedle{offset:02d}",
                "expected_source_ids": [],
            }
        )

    if len(rows) != int(counts["total"]):
        raise ScaleBenchmarkError("ERROR_SCALE_QUERY_BUILD_ARITHMETIC")
    if len({row["query_id"] for row in rows}) != len(rows):
        raise ScaleBenchmarkError("ERROR_SCALE_QUERY_ID_DUPLICATE")
    return rows


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return float(ordered[index])


def _rss_mib() -> float:
    raw = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform == "darwin":
        return raw / (1024.0 * 1024.0)
    return raw / 1024.0


def _citation_metrics(
    query: str,
    retrieved: list[dict[str, Any]],
    expected_source_ids: set[str],
) -> dict[str, Any]:
    model = generate_answer(
        query,
        retrieved,
        model_provider="mock",
        conversation_context=[],
        memory_context=[],
    )
    cited_ids = [str(value) for value in model.get("cited_chunk_ids", [])]
    cited_chunks = build_citation_records(retrieved, cited_ids)
    trace = {
        "retrieved_chunk_ids": [str(chunk["chunk_id"]) for chunk in retrieved],
        "cited_chunk_ids": cited_ids,
        "cited_chunks": cited_chunks,
        "validation_flags": [],
    }
    validated = validate_citations(trace)
    validation_flags = sorted(str(flag) for flag in validated.get("validation_flags", []))
    correctness = not any(flag.startswith("ERROR_") for flag in validation_flags)
    cited_source_ids = {
        str(record.get("source_id"))
        for record in cited_chunks
        if isinstance(record, dict) and record.get("source_id")
    }
    completeness = expected_source_ids.issubset(cited_source_ids)

    normalized_answer = " ".join(str(model.get("answer") or "").split())
    source_claims = 0
    supported_claims = 0
    for chunk_id in cited_ids:
        matched = next((chunk for chunk in retrieved if str(chunk.get("chunk_id")) == chunk_id), None)
        if matched is None:
            continue
        source_claims += 1
        snippet = " ".join(str(matched.get("text") or "").split())[:240]
        if snippet and snippet in normalized_answer:
            supported_claims += 1

    faithfulness = supported_claims / source_claims if source_claims else 0.0
    unsupported_source_claim_rate = (
        (source_claims - supported_claims) / source_claims if source_claims else 1.0
    )
    return {
        "citation_correct": correctness,
        "citation_complete": completeness,
        "faithful": faithfulness == 1.0,
        "unsupported_source_claim_rate": unsupported_source_claim_rate,
        "validation_flags": validation_flags,
        "cited_source_ids": sorted(cited_source_ids),
        "source_claims": source_claims,
        "supported_source_claims": supported_claims,
    }


def evaluate_queries(
    chunks: list[dict[str, Any]],
    queries: list[dict[str, Any]],
    *,
    strategy: str,
    top_k: int,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    latencies_ms: list[float] = []
    positive_rows = 0
    expected_source_hits = 0
    expected_bindings = 0
    expected_binding_hits = 0
    ambiguous_rows = 0
    ambiguous_full_hits = 0
    negative_rows = 0
    negative_hits = 0
    citation_rows = 0
    citation_correct = 0
    citation_complete = 0
    faithful = 0
    unsupported_source_claims = 0.0

    for case in queries:
        started = time.perf_counter()
        result = retrieve(str(case["query"]), chunks, strategy=strategy, top_k=top_k)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        latencies_ms.append(elapsed_ms)
        category = str(case["category"])
        expected = set(str(value) for value in case["expected_source_ids"])
        retrieved = result.get("retrieved_chunks", []) if isinstance(result, dict) else []
        retrieved_source_ids = [
            str(chunk.get("source_id"))
            for chunk in retrieved
            if isinstance(chunk, dict) and chunk.get("source_id")
        ]

        row: dict[str, Any] = {
            "query_id": case["query_id"],
            "category": category,
            "status": result.get("status") if isinstance(result, dict) else "ERROR_INVALID_RESULT",
            "latency_ms": round(elapsed_ms, 3),
            "expected_source_ids": sorted(expected),
            "retrieved_source_ids": retrieved_source_ids,
        }

        if category == "negative":
            negative_rows += 1
            negative_ok = row["status"] == "ERROR_NO_RETRIEVAL_CONTEXT"
            negative_hits += int(negative_ok)
            row["negative_ok"] = negative_ok
            rows.append(row)
            continue

        positive_rows += 1
        intersection = expected.intersection(retrieved_source_ids)
        hit = bool(intersection)
        expected_source_hits += int(hit)
        expected_bindings += len(expected)
        expected_binding_hits += len(intersection)
        full_hit = expected.issubset(set(retrieved_source_ids))
        if category == "ambiguous":
            ambiguous_rows += 1
            ambiguous_full_hits += int(full_hit)

        citation = _citation_metrics(str(case["query"]), retrieved, expected)
        citation_rows += 1
        citation_correct += int(citation["citation_correct"])
        citation_complete += int(citation["citation_complete"])
        faithful += int(citation["faithful"])
        unsupported_source_claims += float(citation["unsupported_source_claim_rate"])

        row.update(
            {
                "expected_source_hit": hit,
                "expected_binding_recall": len(intersection) / len(expected) if expected else 1.0,
                "full_expected_binding_hit": full_hit,
                "citation": citation,
            }
        )
        rows.append(row)

    total_seconds = sum(latencies_ms) / 1000.0
    return {
        "rows": rows,
        "counts": {
            "queries": len(rows),
            "positive_queries": positive_rows,
            "negative_queries": negative_rows,
            "ambiguous_queries": ambiguous_rows,
            "expected_bindings": expected_bindings,
        },
        "metrics": {
            "expected_source_hit_rate": expected_source_hits / positive_rows if positive_rows else 0.0,
            "expected_binding_recall": expected_binding_hits / expected_bindings if expected_bindings else 0.0,
            "ambiguous_full_hit_rate": ambiguous_full_hits / ambiguous_rows if ambiguous_rows else 0.0,
            "negative_query_accuracy": negative_hits / negative_rows if negative_rows else 0.0,
            "citation_correctness": citation_correct / citation_rows if citation_rows else 0.0,
            "citation_completeness": citation_complete / citation_rows if citation_rows else 0.0,
            "faithfulness": faithful / citation_rows if citation_rows else 0.0,
            "unsupported_source_claim_rate": (
                unsupported_source_claims / citation_rows if citation_rows else 1.0
            ),
            "p50_latency_ms": statistics.median(latencies_ms) if latencies_ms else 0.0,
            "p95_latency_ms": _p95(latencies_ms),
            "max_latency_ms": max(latencies_ms, default=0.0),
            "throughput_qps": len(rows) / total_seconds if total_seconds > 0 else 0.0,
            "retrieval_time_seconds": total_seconds,
        },
    }


def _probe_scale(
    chunk_count: int,
    queries: list[dict[str, Any]],
    *,
    strategy: str,
    top_k: int,
    probe_count: int,
    warmup_count: int,
) -> dict[str, Any]:
    chunks = build_corpus(chunk_count)
    positive = [row for row in queries if row["category"] != "negative"]
    probes = positive[:probe_count]
    for case in positive[:warmup_count]:
        retrieve(str(case["query"]), chunks, strategy=strategy, top_k=top_k)
    measured = evaluate_queries(chunks, probes, strategy=strategy, top_k=top_k)
    return {
        "chunk_count": chunk_count,
        "query_count": len(probes),
        "metrics": measured["metrics"],
    }


def _threshold_failures(
    denominator: dict[str, Any],
    metrics: dict[str, Any],
    scale_probes: list[dict[str, Any]],
    *,
    peak_rss_mib: float,
    wall_seconds: float,
) -> list[str]:
    thresholds = denominator["thresholds"]
    failures: list[str] = []

    minimums = {
        "expected_source_hit_rate": "min_expected_source_hit_rate",
        "expected_binding_recall": "min_expected_binding_recall",
        "ambiguous_full_hit_rate": "min_ambiguous_full_hit_rate",
        "negative_query_accuracy": "min_negative_query_accuracy",
        "citation_correctness": "min_citation_correctness",
        "citation_completeness": "min_citation_completeness",
        "faithfulness": "min_faithfulness",
        "throughput_qps": "min_throughput_qps",
    }
    for metric_name, threshold_name in minimums.items():
        if float(metrics.get(metric_name, 0.0)) < float(thresholds[threshold_name]):
            failures.append(f"{metric_name}<{threshold_name}")

    if float(metrics.get("unsupported_source_claim_rate", 1.0)) > float(
        thresholds["max_unsupported_source_claim_rate"]
    ):
        failures.append("unsupported_source_claim_rate>max_unsupported_source_claim_rate")
    if float(metrics.get("p95_latency_ms", math.inf)) > float(thresholds["max_p95_latency_ms"]):
        failures.append("p95_latency_ms>max_p95_latency_ms")
    if float(metrics.get("max_latency_ms", math.inf)) > float(thresholds["max_single_query_latency_ms"]):
        failures.append("max_latency_ms>max_single_query_latency_ms")
    if peak_rss_mib > float(thresholds["max_peak_rss_mib"]):
        failures.append("peak_rss_mib>max_peak_rss_mib")
    if wall_seconds > float(denominator["execution"]["max_runtime_seconds"]):
        failures.append("wall_seconds>max_runtime_seconds")

    by_chunks = {int(row["chunk_count"]): row for row in scale_probes}
    if 1000 in by_chunks and 10000 in by_chunks:
        low = float(by_chunks[1000]["metrics"]["p95_latency_ms"])
        high = float(by_chunks[10000]["metrics"]["p95_latency_ms"])
        growth = high / max(low, 0.001)
        if growth > float(thresholds["max_p95_growth_ratio_1000_to_10000"]):
            failures.append("p95_growth_ratio>max_p95_growth_ratio_1000_to_10000")
    return sorted(failures)


def run_scale_benchmark(
    denominator_path: Path = DEFAULT_DENOMINATOR,
    *,
    scale_override: int | None = None,
) -> dict[str, Any]:
    denominator = load_denominator(denominator_path)
    queries = build_queries(denominator)
    strategy = str(denominator["execution"]["strategy"])
    top_k = int(denominator["corpus"]["top_k"])
    levels = list(int(value) for value in denominator["corpus"]["scale_levels"])
    if scale_override is not None:
        levels = [int(scale_override)]
    started = time.perf_counter()

    scale_probes = [
        _probe_scale(
            level,
            queries,
            strategy=strategy,
            top_k=top_k,
            probe_count=int(denominator["execution"]["probe_queries_per_scale"]),
            warmup_count=int(denominator["execution"]["warmup_queries"]),
        )
        for level in levels
    ]
    max_chunks = int(scale_override or denominator["corpus"]["max_chunks"])
    full_eval = evaluate_queries(
        build_corpus(max_chunks),
        queries,
        strategy=strategy,
        top_k=top_k,
    )
    wall_seconds = time.perf_counter() - started
    peak_rss_mib = _rss_mib()
    failures = _threshold_failures(
        denominator,
        full_eval["metrics"],
        scale_probes,
        peak_rss_mib=peak_rss_mib,
        wall_seconds=wall_seconds,
    )
    if scale_override is not None:
        failures = [
            failure
            for failure in failures
            if failure not in {
                "p95_growth_ratio>max_p95_growth_ratio_1000_to_10000",
                "wall_seconds>max_runtime_seconds",
            }
        ]

    denominator_bytes = denominator_path.read_bytes()
    result = {
        "schema_version": "mucaro-rt10-scale-cert/1.0",
        "status": STATUS_OK if not failures else STATUS_FAILED,
        "head_sha": current_git_head(),
        "benchmark_id": denominator["benchmark_id"],
        "denominator_sha256": hashlib.sha256(denominator_bytes).hexdigest(),
        "denominator": {
            "query_denominator": denominator["query_denominator"],
            "scale_levels": denominator["corpus"]["scale_levels"],
            "max_chunks": denominator["corpus"]["max_chunks"],
            "thresholds": denominator["thresholds"],
        },
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "peak_rss_mib": round(peak_rss_mib, 3),
            "wall_seconds": round(wall_seconds, 3),
        },
        "scale_probes": scale_probes,
        "full_evaluation": full_eval,
        "threshold_failures": failures,
        "claim_boundary": denominator["claim_boundary"],
        "falsification": denominator["falsification"],
    }
    result["report_sha256"] = stable_hash(result)
    return result
