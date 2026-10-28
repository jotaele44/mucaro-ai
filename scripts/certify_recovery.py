from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.recovery_benchmark import (
    BENCHMARK_PATH,
    certify,
    current_git_head,
    load_benchmark,
    make_benchmark_settings,
    run_benchmark,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute the frozen MATRIX-016 recovery benchmark.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, default=BENCHMARK_PATH)
    args = parser.parse_args()

    benchmark = load_benchmark(args.benchmark)
    with tempfile.TemporaryDirectory(prefix="mucaro-recovery-") as temporary:
        root = Path(temporary)
        report = run_benchmark(benchmark, make_benchmark_settings(root))
    head_sha = os.environ.get("HEAD_SHA") or os.environ.get("GITHUB_SHA") or current_git_head(Path.cwd()) or ""
    if not head_sha:
        git_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=False
        )
        head_sha = git_head.stdout.strip() if git_head.returncode == 0 else ""
    receipt = certify(benchmark, report, head_sha=head_sha)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(receipt["status"])
    print(f"receipt_sha256={receipt['receipt_sha256']}")
    print(f"threshold_failures={receipt['threshold_failures']}")
    return 0 if receipt["status"] == "MATRIX016_RECOVERY_BENCH_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
