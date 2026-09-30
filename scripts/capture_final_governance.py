#!/usr/bin/env python3
"""Write an exact-HEAD governance receipt outside the tracked checkout."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from backend.app.release_scope import (
    RELEASE_DIR,
    REQUIREMENT_STATES,
    capture_state_snapshot,
    compute_release_status,
    sha256_file,
)

ROOT = Path(__file__).resolve().parents[1]
LEDGER = RELEASE_DIR / "acceptance_ledger_v1.json"


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def capture_governance(expected_head: str, output: Path) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", expected_head):
        raise ValueError("expected head must be a full lowercase commit SHA")
    actual_head = _git("rev-parse", "HEAD")
    if actual_head != expected_head:
        raise ValueError(f"head mismatch: expected {expected_head}, observed {actual_head}")
    if _git("status", "--porcelain"):
        raise ValueError("checkout must be clean before final-head governance capture")
    output = output.resolve()
    if output.is_relative_to(ROOT):
        raise ValueError("write final-head receipt outside the tracked repository")
    status = compute_release_status(ROOT)
    if status["validation"]["status"] != "RELEASE_SCOPE_VALID":
        raise ValueError("release scope invalid; cannot issue a governance receipt")
    ledger_path = ROOT / LEDGER
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    rows = ledger["rows"]
    counts = {state: sum(row["state"] == state for row in rows) for state in REQUIREMENT_STATES}
    if len(rows) != sum(counts.values()) or counts != status["denominator"]["by_state"]:
        raise ValueError("acceptance ledger denominator mismatch")
    snapshot = capture_state_snapshot(ROOT)
    if snapshot["repository"]["head_sha"] != actual_head or snapshot["repository"]["dirty_paths"]:
        raise ValueError("snapshot head or dirty-state mismatch")
    receipt = {
        "schema_version": "mucaro-final-head-governance/1.0",
        "status": "GOVERNANCE_CAPTURE_VALID",
        "head_sha": actual_head,
        "acceptance_ledger": {
            "path": LEDGER.as_posix(),
            "sha256": sha256_file(ledger_path),
            "rows": len(rows),
            "counts": counts,
        },
        "release_status": status,
        "state_snapshot": snapshot,
        "limitations": [
            "Capture validates this commit and environment only; it does not execute feature gates.",
            "Credential presence is recorded without values.",
            "A later commit requires a new receipt; the tracked 2026-09-24 snapshot is historical.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = capture_governance(args.expected_head, args.output)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"status": "GOVERNANCE_CAPTURE_FAILED", "detail": str(exc)}))
        return 2
    print(json.dumps({"status": receipt["status"], "head_sha": receipt["head_sha"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
