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

    report = run_scale_benchmark(scale_override=args.scale_override)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(report["status"])
    print(f"report_sha256={report['report_sha256']}")
    print(f"threshold_failures={report['threshold_failures']}")
    return 0 if report["status"] == "RT10_SCALE_CERT_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
