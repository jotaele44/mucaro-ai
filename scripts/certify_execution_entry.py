#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DENOMINATOR = ROOT / "_architecture" / "execution_entry_denominator_v1.json"
TEST_FILE = ROOT / "backend" / "tests" / "test_execution_entry.py"
CONTRACT = ROOT / "docs" / "GOAL_PLAN_EXECUTION_ENTRY_V1.md"
STATUS_OK = "MUCARO_EXECUTION_ENTRY_BACKEND_CERT_OK"
CASE_RE = re.compile(r"^test_([pn]\d{2})_", re.IGNORECASE)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_head() -> str:
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


def test_case_ids() -> list[str]:
    tree = ast.parse(TEST_FILE.read_text(encoding="utf-8"), filename=str(TEST_FILE))
    result = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            match = CASE_RE.match(node.name)
            if match:
                result.append(match.group(1).upper())
    return sorted(result)


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify the exact 28-case Mucaro Goal/Plan execution-entry backend denominator.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--head-sha", help="Exact expected Git SHA; defaults to current HEAD.")
    args = parser.parse_args()

    observed_head = git_head()
    expected_head = (args.head_sha or observed_head).strip()
    denominator = json.loads(DENOMINATOR.read_text(encoding="utf-8"))
    expected_ids = sorted(row["case_id"] for row in denominator["cases"])
    observed_ids = test_case_ids()

    command = ["python", "-m", "pytest", str(TEST_FILE.relative_to(ROOT)), "-q"]
    proc = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    match = re.search(r"(\d+) passed", proc.stdout)
    passed = int(match.group(1)) if match else 0
    terminal_checks = {
        "head_matches": observed_head == expected_head,
        "denominator_declares_28": denominator.get("expected_count") == 28,
        "positive_arithmetic": denominator.get("positive_count") == 8,
        "negative_arithmetic": denominator.get("negative_count") == 20,
        "case_ids_exact": observed_ids == expected_ids,
        "case_count_exact": len(observed_ids) == 28 == len(expected_ids),
        "pytest_exit_zero": proc.returncode == 0,
        "pytest_28_passed": passed == 28,
    }
    status = STATUS_OK if all(terminal_checks.values()) else "MUCARO_EXECUTION_ENTRY_BACKEND_CERT_FAILED"
    receipt = {
        "schema_version": "mucaro-execution-entry-backend-cert/1.0",
        "status": status,
        "head_sha": observed_head,
        "expected_head_sha": expected_head,
        "denominator_sha256": sha256_file(DENOMINATOR),
        "contract_sha256": sha256_file(CONTRACT),
        "test_file_sha256": sha256_file(TEST_FILE),
        "expected_case_ids": expected_ids,
        "observed_case_ids": observed_ids,
        "arithmetic": {
            "expected": 28,
            "observed": len(observed_ids),
            "positive": denominator.get("positive_count"),
            "negative": denominator.get("negative_count"),
            "pytest_passed": passed,
        },
        "terminal_checks": terminal_checks,
        "pytest_command": command,
        "pytest_output": proc.stdout[-12000:],
        "limitations": [
            "This receipt certifies the backend entry controller denominator only.",
            "Rendered desktop/mobile, keyboard, screen-reader and locale-switch evidence remains a separate required gate.",
            "This receipt does not certify downstream tool side effects, signing, physical device evidence, live web, or live LLM providers.",
        ],
    }
    material = dict(receipt)
    receipt["receipt_sha256"] = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    path = output / "execution-entry-backend-receipt.json"
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(status)
    print(json.dumps({
        "head_sha": observed_head,
        "receipt_sha256": receipt["receipt_sha256"],
        "arithmetic": receipt["arithmetic"],
        "failed_terminal_checks": [name for name, passed_check in terminal_checks.items() if not passed_check],
    }, sort_keys=True))
    return 0 if status == STATUS_OK else 2


if __name__ == "__main__":
    raise SystemExit(main())
