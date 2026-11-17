from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from backend.app import tool_governance_benchmark
from backend.app.tool_governance_benchmark import (
    CASES,
    REQUIRED_CATEGORIES,
    STATUS_FAILED,
    STATUS_OK,
    certify_tool_governance_benchmark,
)
from backend.app.tools import TOOL_REGISTRY

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "_architecture" / "tool_governance_benchmark_v1.json"
EXPECTED_CASES = [
    "ALLOWED_EXECUTES_AND_AUDITS",
    "UNKNOWN_TOOL_REFUSED",
    "DENIED_NEVER_EXECUTES",
    "APPROVAL_REQUIRED_BEFORE_RUN",
    "CANCELLED_NEVER_EXECUTES",
    "TOOL_ERROR_RECORDED",
    "TIMEOUT_NEVER_EXECUTED",
    "RETRY_NEEDS_FRESH_APPROVAL",
    "RESTART_INTERRUPTED_NOT_RERUN",
    "DUPLICATE_INVOCATION_REFUSED",
    "FILESYSTEM_GRANT_ENFORCED",
    "TOO_BROAD_GRANT_REFUSED",
    "NETWORK_PRIVATE_AND_LIVE_GATES",
    "MODEL_DENIED_BEFORE_TRANSPORT",
    "SIDE_EFFECTS_ASK_FOR_AGENT",
    "DISTRIBUTION_CHANNEL_ENFORCED",
    "UNKNOWN_INPUTS_DENIED",
    "PLANNING_NEVER_AUTHORIZES",
    "UNDECLARED_ESCALATION_DENIED",
    "ARGUMENTS_BOUND_TO_APPROVAL",
    "AUDIT_TAMPER_BLOCKS_APPROVAL",
    "MEMORY_CANNOT_AUTHORIZE",
    "LEASE_REVOCATION_ENFORCED",
]


def _project_copy(tmp_path: Path, manifest: dict) -> Path:
    """A project root whose manifest is replaced, so the runner sees a drifted denominator."""
    root = tmp_path / "project"
    (root / "_architecture").mkdir(parents=True)
    (root / "_architecture" / "tool_governance_benchmark_v1.json").write_text(json.dumps(manifest), encoding="utf-8")
    shutil.copytree(ROOT / "backend", root / "backend", ignore=shutil.ignore_patterns("__pycache__", "tests"))
    return root


def test_matrix011_frozen_manifest_matches_runtime_declaration() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert [case["case_id"] for case in manifest["cases"]] == EXPECTED_CASES
    assert manifest["required_case_count"] == len(EXPECTED_CASES) == len(CASES)
    assert set(CASES) == set(EXPECTED_CASES)
    assert manifest["required_categories"] == list(REQUIRED_CATEGORIES)
    assert set(REQUIRED_CATEGORIES) <= {case["category"] for case in manifest["cases"]}
    assert manifest["limitations"], "known limits must be stated in the frozen manifest"


def test_matrix011_full_benchmark_closes_its_frozen_denominator(tmp_path: Path) -> None:
    tools_before = set(TOOL_REGISTRY)

    receipt = certify_tool_governance_benchmark(ROOT, tmp_path / "workspace", head_sha="a" * 40)

    assert receipt["status"] == STATUS_OK, [row for row in receipt["case_rows"] if not row["passed"]]
    assert receipt["head_sha"] == "a" * 40
    assert receipt["arithmetic"] == {
        "case_count_expected": 23,
        "case_count_observed": 23,
        "case_pass": 23,
        "case_fail": 0,
        "arithmetic_closes": True,
    }
    assert [row["case_id"] for row in receipt["case_rows"]] == EXPECTED_CASES
    assert all(receipt["terminal_checks"].values())
    assert receipt["benchmark_manifest_sha256"] == tool_governance_benchmark._sha256_bytes(MANIFEST.read_bytes())
    assert set(TOOL_REGISTRY) == tools_before, "benchmark tools must not leak into the registry"


def test_matrix011_a_case_with_the_wrong_outcome_fails_the_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(CASES, "ARGUMENTS_BOUND_TO_APPROVAL", lambda cfg, root: ("CHANGED_ARGUMENTS_ACCEPTED", {}))

    receipt = certify_tool_governance_benchmark(ROOT, tmp_path / "workspace")

    assert receipt["status"] == STATUS_FAILED
    assert receipt["arithmetic"]["case_fail"] == 1
    assert {"all_cases_pass", "zero_unexpected_outcomes"} <= set(receipt["failed_checks"])


def test_matrix011_a_dropped_case_fails_the_denominator(tmp_path: Path) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["cases"] = [case for case in manifest["cases"] if case["case_id"] != "PLANNING_NEVER_AUTHORIZES"]

    receipt = certify_tool_governance_benchmark(_project_copy(tmp_path, manifest), tmp_path / "workspace")

    assert receipt["status"] == STATUS_FAILED
    assert receipt["case_rows"] == []
    assert {"case_count_matches_declaration", "every_handler_is_declared"} <= set(receipt["failed_checks"])


def test_matrix011_a_crashing_case_is_recorded_as_a_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(cfg, root):
        raise RuntimeError("store unavailable")

    monkeypatch.setitem(CASES, "UNKNOWN_INPUTS_DENIED", boom)

    receipt = certify_tool_governance_benchmark(ROOT, tmp_path / "workspace")

    row = next(row for row in receipt["case_rows"] if row["case_id"] == "UNKNOWN_INPUTS_DENIED")
    assert row["observed_outcome"] == "CASE_ERROR:RuntimeError"
    assert receipt["status"] == STATUS_FAILED
