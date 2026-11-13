from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

BENCHMARK_PATH = Path("_architecture/performance_benchmark_v1.json")
SCHEMA_VERSION = "MUCARO_MATRIX017_PERFORMANCE/1.0"


class PerformanceBenchmarkError(RuntimeError):
    pass


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_benchmark(path: Path = BENCHMARK_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise PerformanceBenchmarkError("ERROR_PERFORMANCE_SCHEMA")
    profile = payload.get("profile")
    workload = payload.get("workload")
    thresholds = payload.get("thresholds")
    if not all(isinstance(item, dict) for item in (profile, workload, thresholds)):
        raise PerformanceBenchmarkError("ERROR_PERFORMANCE_STRUCTURE")
    if profile.get("provider") != "mock":
        raise PerformanceBenchmarkError("ERROR_PERFORMANCE_PROVIDER")
    if profile.get("required_machine") != "arm64" or profile.get("required_os") != "Darwin":
        raise PerformanceBenchmarkError("ERROR_PERFORMANCE_PROFILE")
    runs = workload.get("runs")
    questions = workload.get("questions")
    if not isinstance(runs, int) or runs < 1:
        raise PerformanceBenchmarkError("ERROR_PERFORMANCE_RUNS")
    if not isinstance(questions, list) or not questions or not all(isinstance(q, str) and q.strip() for q in questions):
        raise PerformanceBenchmarkError("ERROR_PERFORMANCE_QUESTIONS")
    required_thresholds = {
        "min_completed_rate",
        "max_error_count",
        "max_p95_first_word_ms",
        "max_p95_total_ms",
    }
    if set(thresholds) != required_thresholds:
        raise PerformanceBenchmarkError("ERROR_PERFORMANCE_THRESHOLDS")
    if not 0 < float(thresholds["min_completed_rate"]) <= 1:
        raise PerformanceBenchmarkError("ERROR_PERFORMANCE_THRESHOLDS")
    if int(thresholds["max_error_count"]) < 0:
        raise PerformanceBenchmarkError("ERROR_PERFORMANCE_THRESHOLDS")
    if float(thresholds["max_p95_first_word_ms"]) <= 0 or float(thresholds["max_p95_total_ms"]) <= 0:
        raise PerformanceBenchmarkError("ERROR_PERFORMANCE_THRESHOLDS")
    return payload


def threshold_failures(benchmark: dict[str, Any], report: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    thresholds = benchmark["thresholds"]
    runs = int(report.get("runs") or 0)
    completed = int(report.get("completed") or 0)
    completed_rate = completed / runs if runs else 0.0
    error_count = len(report.get("errors") or [])
    first_p95 = (report.get("first_word_ms") or {}).get("p95")
    total_p95 = (report.get("total_ms") or {}).get("p95")

    if report.get("status") != "BENCH_OK":
        failures.append("bench_status!=BENCH_OK")
    if completed_rate < float(thresholds["min_completed_rate"]):
        failures.append("completed_rate<min_completed_rate")
    if error_count > int(thresholds["max_error_count"]):
        failures.append("error_count>max_error_count")
    if first_p95 is None or float(first_p95) > float(thresholds["max_p95_first_word_ms"]):
        failures.append("p95_first_word_ms>max_p95_first_word_ms")
    if total_p95 is None or float(total_p95) > float(thresholds["max_p95_total_ms"]):
        failures.append("p95_total_ms>max_p95_total_ms")
    return failures


def certify(
    benchmark: dict[str, Any],
    report: dict[str, Any],
    *,
    os_name: str,
    machine: str,
    python_version: str,
    head_sha: str,
    workflow_run_id: str | None = None,
) -> dict[str, Any]:
    profile = benchmark["profile"]
    failures = threshold_failures(benchmark, report)
    if os_name != profile["required_os"]:
        failures.append("os!=required_os")
    if machine != profile["required_machine"]:
        failures.append("machine!=required_machine")
    if not python_version.startswith(str(profile["python_version"])):
        failures.append("python_version!=required_python_version")
    if len(head_sha) != 40:
        failures.append("head_sha_invalid")

    receipt = {
        "schema_version": "mucaro-matrix017-performance-receipt/1.0",
        "status": "MATRIX017_PERFORMANCE_CERT_PASS" if not failures else "MATRIX017_PERFORMANCE_CERT_FAIL",
        "benchmark_id": benchmark["benchmark_id"],
        "benchmark_sha256": stable_hash(benchmark),
        "head_sha": head_sha,
        "workflow_run_id": workflow_run_id,
        "observed_profile": {
            "os": os_name,
            "machine": machine,
            "python_version": python_version,
        },
        "workload": benchmark["workload"],
        "thresholds": benchmark["thresholds"],
        "measurements": report,
        "threshold_failures": sorted(set(failures)),
        "claim_boundary": benchmark["claim_boundary"],
    }
    receipt["receipt_sha256"] = stable_hash(receipt)
    return receipt
