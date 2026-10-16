from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.performance_benchmark import (
    PerformanceBenchmarkError,
    certify,
    load_benchmark,
    stable_hash,
    threshold_failures,
)


def _passing_report() -> dict:
    return {
        "status": "BENCH_OK",
        "provider": "mock",
        "runs": 20,
        "completed": 20,
        "errors": [],
        "first_word_ms": {"p50": 50.0, "p95": 100.0},
        "total_ms": {"p50": 100.0, "p95": 200.0},
        "stage_median_ms": {"retrieval": 10.0},
    }


def test_matrix017_benchmark_is_frozen_and_complete() -> None:
    benchmark = load_benchmark()
    assert benchmark["benchmark_id"] == "mucaro-matrix017-workrun-v1"
    assert benchmark["profile"] == {
        "runner": "macos-14",
        "required_os": "Darwin",
        "required_machine": "arm64",
        "python_version": "3.11",
        "provider": "mock",
    }
    assert benchmark["workload"]["runs"] == 20
    assert len(benchmark["workload"]["questions"]) == 3
    assert benchmark["thresholds"] == {
        "min_completed_rate": 1.0,
        "max_error_count": 0,
        "max_p95_first_word_ms": 5000.0,
        "max_p95_total_ms": 10000.0,
    }


def test_matrix017_passing_receipt_binds_profile_head_and_benchmark() -> None:
    benchmark = load_benchmark()
    receipt = certify(
        benchmark,
        _passing_report(),
        os_name="Darwin",
        machine="arm64",
        python_version="3.11.9",
        head_sha="a" * 40,
        workflow_run_id="123",
    )
    assert receipt["status"] == "MATRIX017_PERFORMANCE_CERT_PASS"
    assert receipt["threshold_failures"] == []
    assert receipt["benchmark_sha256"] == stable_hash(benchmark)
    assert len(receipt["receipt_sha256"]) == 64


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ({"completed": 19}, "completed_rate<min_completed_rate"),
        ({"errors": ["ERROR"]}, "error_count>max_error_count"),
        ({"first_word_ms": {"p50": 1.0, "p95": 5000.1}}, "p95_first_word_ms>max_p95_first_word_ms"),
        ({"total_ms": {"p50": 1.0, "p95": 10000.1}}, "p95_total_ms>max_p95_total_ms"),
        ({"status": "BENCH_PARTIAL"}, "bench_status!=BENCH_OK"),
    ],
)
def test_matrix017_threshold_falsification_is_detected(mutation: dict, expected: str) -> None:
    benchmark = load_benchmark()
    report = _passing_report()
    report.update(mutation)
    assert expected in threshold_failures(benchmark, report)


def test_matrix017_wrong_machine_fails_closed() -> None:
    receipt = certify(
        load_benchmark(),
        _passing_report(),
        os_name="Darwin",
        machine="x86_64",
        python_version="3.11.9",
        head_sha="a" * 40,
    )
    assert receipt["status"] == "MATRIX017_PERFORMANCE_CERT_FAIL"
    assert "machine!=required_machine" in receipt["threshold_failures"]


def test_matrix017_threshold_mutation_requires_valid_shape(tmp_path: Path) -> None:
    benchmark = load_benchmark()
    mutated = json.loads(json.dumps(benchmark))
    mutated["thresholds"].pop("max_p95_total_ms")
    path = tmp_path / "benchmark.json"
    path.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(PerformanceBenchmarkError, match="ERROR_PERFORMANCE_THRESHOLDS"):
        load_benchmark(path)
