from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "mucaro-ingestion-benchmark-receipt/1.0"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"expected object: {path}")
    return value


def _head(root: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit("cannot resolve Git HEAD")
    return proc.stdout.strip()


def certify(root: Path, manifest_path: Path) -> dict[str, Any]:
    root = root.resolve()
    manifest_path = manifest_path.resolve()
    manifest = _load_object(manifest_path)
    registry_path = root / str(manifest["registry_path"])
    registry = _load_object(registry_path)

    errors: list[str] = []
    if manifest.get("schema_version") != "mucaro-ingestion-benchmark/1.0":
        errors.append("manifest_schema")
    if manifest.get("benchmark_id") != "MATRIX-010":
        errors.append("benchmark_id")
    if registry.get("schema_version") != manifest.get("expected_registry_schema_version"):
        errors.append("registry_schema")
    if registry.get("format_count") != manifest.get("expected_format_count"):
        errors.append("registry_format_count")

    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("cases")
        cases = []

    case_ids = [str(row.get("case_id")) for row in cases if isinstance(row, dict)]
    if len(case_ids) != len(set(case_ids)):
        errors.append("duplicate_case_id")

    representative = {
        str(row.get("handling_class"))
        for row in cases
        if isinstance(row, dict) and row.get("kind") == "representative"
    }
    expected_representative = set(manifest.get("representative_handling_classes") or [])
    if representative != expected_representative:
        errors.append("representative_denominator")

    adversarial = {
        str(row.get("adversarial_class"))
        for row in cases
        if isinstance(row, dict) and row.get("kind") == "adversarial"
    }
    expected_adversarial = set(manifest.get("required_adversarial_classes") or [])
    if adversarial != expected_adversarial:
        errors.append("adversarial_denominator")

    registry_classes = {str(row.get("handling")) for row in registry.get("formats") or []}
    if not expected_representative.issubset(registry_classes):
        errors.append("registry_handling_coverage")

    results: list[dict[str, Any]] = []
    if not errors:
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = "0"
        for case in cases:
            node = str(case["node"])
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", node, "--disable-warnings"],
                cwd=root,
                text=True,
                capture_output=True,
                env=env,
                check=False,
                timeout=120,
            )
            results.append(
                {
                    "case_id": case["case_id"],
                    "kind": case["kind"],
                    "handling_class": case.get("handling_class"),
                    "adversarial_class": case.get("adversarial_class"),
                    "node": node,
                    "status": "PASS" if proc.returncode == 0 else "FAIL",
                    "exit_code": proc.returncode,
                }
            )

    passed = sum(row["status"] == "PASS" for row in results)
    failed = sum(row["status"] == "FAIL" for row in results)
    required = len(cases)
    arithmetic_closes = passed + failed == required
    status = (
        "MATRIX010_INGESTION_BENCHMARK_PASS"
        if not errors and arithmetic_closes and failed == 0 and required == 16
        else "MATRIX010_INGESTION_BENCHMARK_FAIL"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "benchmark_id": manifest.get("benchmark_id"),
        "benchmark_revision": manifest.get("revision"),
        "head_sha": _head(root),
        "manifest_path": str(manifest_path.relative_to(root)),
        "manifest_sha256": _sha256(manifest_path),
        "registry_path": str(registry_path.relative_to(root)),
        "registry_sha256": _sha256(registry_path),
        "registry_format_count": registry.get("format_count"),
        "representative_handling_classes": sorted(representative),
        "adversarial_classes": sorted(adversarial),
        "required_case_count": required,
        "passed_case_count": passed,
        "failed_case_count": failed,
        "arithmetic_closes": arithmetic_closes,
        "validation_errors": errors,
        "cases": results,
        "claim_boundary": [
            "Representative supported formats are sampled by declared handling class; this receipt does not claim every one of the 22 suffixes was parsed in this benchmark.",
            "The complete 22-format declaration remains governed by the canonical registry parity regressions.",
            "A PASS certifies the MATRIX-010 representative/adversarial benchmark only; it does not certify unrelated release rows.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("_architecture/ingestion_benchmark_v1.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    receipt = certify(root, root / args.manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["status"] == "MATRIX010_INGESTION_BENCHMARK_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
