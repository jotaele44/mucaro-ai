from __future__ import annotations

import json
from pathlib import Path

from scripts.certify_release_grade import EXTERNAL_EVIDENCE_SCHEMA, external_gate


def test_external_gate_requires_receipt(tmp_path: Path) -> None:
    gate = external_gate("clean_machine_macos", None, expected_head="abc", required_true=("install",))
    assert gate.status == "BLOCKED"


def test_external_gate_binds_exact_head_and_required_evidence(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    path.write_text(
        json.dumps({
            "schema_version": EXTERNAL_EVIDENCE_SCHEMA,
            "head_sha": "abc123",
            "evidence": {"install": True, "launch": True, "rollback": True},
        }),
        encoding="utf-8",
    )
    passed = external_gate(
        "clean_machine_macos",
        path,
        expected_head="abc123",
        required_true=("install", "launch", "rollback"),
    )
    assert passed.status == "PASS"
    stale = external_gate(
        "clean_machine_macos",
        path,
        expected_head="different",
        required_true=("install", "launch", "rollback"),
    )
    assert stale.status == "FAIL"


def test_external_gate_fails_when_required_boolean_is_missing(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    path.write_text(
        json.dumps({
            "schema_version": EXTERNAL_EVIDENCE_SCHEMA,
            "head_sha": "abc123",
            "evidence": {"signed": True, "notarized": False},
        }),
        encoding="utf-8",
    )
    gate = external_gate(
        "signed_notarized_installer",
        path,
        expected_head="abc123",
        required_true=("signed", "notarized"),
    )
    assert gate.status == "FAIL"
    assert "notarized" in gate.detail
