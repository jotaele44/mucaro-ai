from __future__ import annotations

import json
from pathlib import Path

from backend.app.recovery_benchmark import (
    BENCHMARK_PATH,
    certify,
    load_benchmark,
    make_benchmark_settings,
    run_benchmark,
)


def test_recovery_benchmark_fixture_is_frozen_and_ordered() -> None:
    benchmark = load_benchmark(BENCHMARK_PATH)
    assert benchmark["benchmark_id"] == "mucaro-matrix016-recovery-v1"
    assert [row["id"] for row in benchmark["scenarios"]] == [
        "process_restart",
        "waiting_resume",
        "fifo_idempotency",
        "partial_failure_retry",
        "offline_transition",
    ]
    assert benchmark["thresholds"] == {"min_pass_rate": 1.0, "max_failed_scenarios": 0}


def test_recovery_benchmark_passes_in_isolated_store(tmp_path: Path) -> None:
    benchmark = load_benchmark()
    report = run_benchmark(benchmark, make_benchmark_settings(tmp_path))
    assert report["status"] == "RECOVERY_BENCH_OK"
    assert report["passed"] == report["scenario_count"] == 5
    assert report["failed"] == 0
    assert report["arithmetic_closes"] is True


def test_recovery_certification_fails_closed_on_missing_scenario(tmp_path: Path) -> None:
    benchmark = load_benchmark()
    report = run_benchmark(benchmark, make_benchmark_settings(tmp_path))
    report["failed"] = 1
    report["status"] = "RECOVERY_BENCH_FAIL"
    receipt = certify(benchmark, report, head_sha="a" * 40)
    assert receipt["status"] == "MATRIX016_RECOVERY_BENCH_FAIL"
    assert "bench_status!=RECOVERY_BENCH_OK" in receipt["threshold_failures"]
    assert "failed_scenarios>max_failed_scenarios" in receipt["threshold_failures"]


def test_recovery_fixture_is_valid_json() -> None:
    payload = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "MUCARO_MATRIX016_RECOVERY/1.0"
