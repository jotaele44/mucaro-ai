from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

from backend.app.performance_benchmark import certify, load_benchmark


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute the frozen MATRIX-017 work-run performance benchmark.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, default=Path("_architecture/performance_benchmark_v1.json"))
    args = parser.parse_args()

    benchmark = load_benchmark(args.benchmark)
    workload = benchmark["workload"]
    command = [
        sys.executable,
        "scripts/bench_latency.py",
        "--provider",
        benchmark["profile"]["provider"],
        "--runs",
        str(workload["runs"]),
        "--corpus",
        workload["corpus"],
        "--format",
        "json",
    ]
    for question in workload["questions"]:
        command.extend(["--question", question])

    proc = subprocess.run(command, text=True, capture_output=True, check=False)
    try:
        report = json.loads(proc.stdout) if proc.stdout.strip() else {
            "status": "BENCH_EXECUTION_FAILED",
            "runs": workload["runs"],
            "completed": 0,
            "errors": ["NO_JSON_OUTPUT"],
        }
    except json.JSONDecodeError:
        report = {
            "status": "BENCH_EXECUTION_FAILED",
            "runs": workload["runs"],
            "completed": 0,
            "errors": ["INVALID_JSON_OUTPUT"],
        }
    if proc.returncode != 0 and report.get("status") == "BENCH_OK":
        report["status"] = "BENCH_EXECUTION_FAILED"
        report["errors"] = sorted(set((report.get("errors") or []) + [f"BENCH_EXIT_{proc.returncode}"]))

    head_sha = os.environ.get("HEAD_SHA") or os.environ.get("GITHUB_SHA") or ""
    receipt = certify(
        benchmark,
        report,
        os_name=platform.system(),
        machine=platform.machine(),
        python_version=platform.python_version(),
        head_sha=head_sha,
        workflow_run_id=os.environ.get("GITHUB_RUN_ID"),
    )
    if proc.stderr.strip():
        receipt["diagnostic_stderr_tail"] = proc.stderr[-2000:]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(receipt["status"])
    print(f"receipt_sha256={receipt['receipt_sha256']}")
    print(f"threshold_failures={receipt['threshold_failures']}")
    return 0 if receipt["status"] == "MATRIX017_PERFORMANCE_CERT_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
