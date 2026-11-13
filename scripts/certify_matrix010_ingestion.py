#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from backend.app.ingestion_benchmark import STATUS_OK, certify_ingestion_benchmark

ROOT = Path(__file__).resolve().parents[1]


def _git_head() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"GIT_HEAD_UNAVAILABLE:{proc.stdout.strip()}")
    return proc.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify MATRIX-010 across the frozen 22-format ingestion denominator.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--head-sha", help="Exact source SHA represented by this execution; defaults to git HEAD.")
    args = parser.parse_args()

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    head_sha = (args.head_sha or _git_head()).strip()

    with tempfile.TemporaryDirectory(prefix="mucaro-matrix010-") as temporary:
        receipt = certify_ingestion_benchmark(ROOT, Path(temporary), head_sha=head_sha)

    receipt_path = output / "matrix010-ingestion-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(receipt["status"])
    print(json.dumps({
        "head_sha": receipt.get("head_sha"),
        "receipt_sha256": receipt.get("receipt_sha256"),
        "arithmetic": receipt.get("arithmetic"),
        "failed_terminal_checks": [
            name for name, passed in (receipt.get("terminal_checks") or {}).items() if not passed
        ],
    }, sort_keys=True))
    return 0 if receipt["status"] == STATUS_OK else 2


if __name__ == "__main__":
    raise SystemExit(main())
