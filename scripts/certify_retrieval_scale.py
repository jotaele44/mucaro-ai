from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.app.retrieval_scale_benchmark import run_scale_benchmark


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute the frozen RT-10/MATRIX-007 retrieval scale benchmark.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scale-override", type=int, default=None)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        report = run_scale_benchmark(scale_override=args.scale_override)
    except Exception as exc:
        report = {
            "schema_version": "mucaro-rt10-scale-cert-failure/1.0",
            "status": "RT10_SCALE_CERT_FAIL",
            "failure_stage": "benchmark_exception",
            "exception_type": type(exc).__name__,
            "limitations": [
                "The benchmark did not produce a valid certification report.",
                "This failure receipt is diagnostic only and never authorizes denominator promotion.",
            ],
        }
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(report["status"])
    if report.get("report_sha256"):
        print(f"report_sha256={report['report_sha256']}")
    print(f"threshold_failures={report.get('threshold_failures', [])}")
    if report.get("exception_type"):
        print(f"exception_type={report['exception_type']}")
    return 0 if report["status"] == "RT10_SCALE_CERT_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
