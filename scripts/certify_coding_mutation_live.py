from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from backend.app.coding_container import execute_coding_container
from backend.app.config import Settings

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "backend/tests/fixtures/coding_mutation_live_probe.txt"
STATUS_OK = "CODING_MUTATION_LIVE_CERT_OK"
STATUS_FAILED = "CODING_MUTATION_LIVE_CERT_FAILED"


def _run(image: str, mode: str, cfg: Settings) -> dict[str, Any]:
    receipt = execute_coding_container(
        ROOT,
        image=image,
        command=["python", "scripts/coding_mutation_probe_inner.py", "--mode", mode],
        cfg=cfg,
    )
    output_lines = str(receipt.get("stdout", {}).get("text") or "").strip().splitlines()
    payload = json.loads(output_lines[-1]) if output_lines else {}
    return {"execution": receipt, "payload": payload}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Certify governed patch mutation inside the ephemeral coding container."
    )
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    before = PROBE.read_bytes()
    before_sha = hashlib.sha256(before).hexdigest()
    cfg = Settings.load()

    positive = _run(args.image, "apply", cfg)
    negative = _run(args.image, "preimage-drift", cfg)
    rollback = _run(args.image, "rollback", cfg)

    after = PROBE.read_bytes()
    after_sha = hashlib.sha256(after).hexdigest()
    invariants = {
        "positive_container_pass": positive["execution"].get("execution_status") == "PASS",
        "positive_mutation_applied_ephemerally": positive["payload"].get("status") == "CODING_MUTATION_APPLIED",
        "positive_postimage_beta": positive["payload"].get("postimage") == "beta\n",
        "negative_container_pass": negative["execution"].get("execution_status") == "PASS",
        "preimage_drift_rejected": negative["payload"].get("expected_rejection") is True,
        "rollback_container_pass": rollback["execution"].get("execution_status") == "PASS",
        "rollback_executed": rollback["payload"].get("expected_rollback") is True,
        "rollback_restored_preimage": rollback["payload"].get("postimage") == "alpha\n",
        "host_fixture_unchanged": before_sha == after_sha and after == before,
        "positive_network_disabled": positive["execution"].get("invariants", {}).get("network_disabled") is True,
        "negative_network_disabled": negative["execution"].get("invariants", {}).get("network_disabled") is True,
        "rollback_network_disabled": rollback["execution"].get("invariants", {}).get("network_disabled") is True,
        "host_source_read_only": positive["execution"].get("invariants", {}).get("host_source_read_only") is True,
    }
    result = {
        "schema_version": "mucaro-coding-mutation-live-cert/1.0",
        "status": STATUS_OK if all(invariants.values()) else STATUS_FAILED,
        "image": args.image,
        "host_probe_sha256_before": before_sha,
        "host_probe_sha256_after": after_sha,
        "positive": positive,
        "negative": negative,
        "rollback": rollback,
        "invariants": invariants,
        "limitations": [
            (
                "This live probe proves positive mutation, preimage-drift rejection and one deterministic "
                "reverse-apply rollback path in the ephemeral isolated coding workspace."
            ),
            "It does not by itself prove every possible rollback/concurrency failure or authenticated remote Git action.",
            "The host checkout is never writable inside the coding container.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(result["status"])
    return 0 if result["status"] == STATUS_OK else 2


if __name__ == "__main__":
    raise SystemExit(main())
