from __future__ import annotations

import json
from pathlib import Path

import scripts.certify_release_grade as grade
from backend.app.release_manifest import build_final_release_manifest
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


# --- macOS app receipts, final manifest and the terminal verdict ---------------------------

HEAD = "c" * 40


def _receipt(tmp_path: Path, name: str, **fields) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(fields), encoding="utf-8")
    return path


def test_receipt_gate_binds_head_status_schema_and_blocking_checks(tmp_path: Path) -> None:
    def check(**fields):
        payload = {"schema_version": grade.MACOS_E2E_SCHEMA, "head_sha": HEAD, "status": "MACOS_APP_E2E_PASS", "blocking_checks": [], **fields}
        return grade.receipt_gate("macos_app_e2e", _receipt(tmp_path, "r.json", **payload), expected_head=HEAD, schema_version=grade.MACOS_E2E_SCHEMA, expected_status="MACOS_APP_E2E_PASS")

    assert grade.receipt_gate("macos_app_e2e", None, expected_head=HEAD, schema_version=grade.MACOS_E2E_SCHEMA, expected_status="X").status == "BLOCKED"
    assert check().status == "PASS"
    assert check(head_sha="d" * 40).status == "FAIL"
    assert check(status="E2E_FAIL").status == "FAIL"
    assert check(schema_version="other/1.0").status == "FAIL"
    assert check(blocking_checks=["window"]).status == "FAIL"


def _run_main(tmp_path: Path, monkeypatch, *, certified_scope: bool) -> tuple[int, dict, str]:
    passed = grade.Gate("stub", "PASS", "ok")
    monkeypatch.setattr(grade, "git_head", lambda: HEAD)
    monkeypatch.setattr(grade, "run_gate", lambda name, command, **_: (grade.Gate(name, "PASS", "ok", command), ""))
    monkeypatch.setattr(grade, "security_profile_gate", lambda *a, **k: grade.Gate("security_profile", passed.status, "ok"))
    monkeypatch.setattr(grade, "developer_environment_security_gate", lambda: grade.Gate("developer_environment_security", "INFO", "local"))
    monkeypatch.setattr(grade, "package_gates", lambda: [grade.Gate("package_build", "PASS", "ok"), grade.Gate("source_artifact_installability", "PASS", "ok")])
    monkeypatch.setattr(grade.subprocess, "run", lambda *a, **k: type("P", (), {"stdout": "", "returncode": 0})())
    monkeypatch.setattr(grade, "build_final_release_manifest", lambda root, **_: {"complete_except_blocked": True, "missing_entries": []})
    monkeypatch.setattr(grade, "write_final_release_manifest", lambda root, record: (grade.ROOT / "data/exports/release_manifests/stub.json", "0" * 64))
    if certified_scope:
        monkeypatch.setattr(grade, "compute_release_status", lambda root: {"status": grade.STATUS_CERTIFIED, "residue": {}})
    captured: dict = {}

    def fake_receipt(root, **kwargs):
        captured.update(kwargs)
        return grade.ROOT / "data/exports/release_receipts/stub.json"

    monkeypatch.setattr(grade, "write_release_receipt", fake_receipt)
    build = _receipt(tmp_path, "build.json", schema_version=grade.MACOS_BUILD_SCHEMA, head_sha=HEAD, status="MACOS_APP_BUILD_OK", blocking_checks=[])
    e2e = _receipt(tmp_path, "e2e.json", schema_version=grade.MACOS_E2E_SCHEMA, head_sha=HEAD, status="MACOS_APP_E2E_PASS", blocking_checks=[])
    signing = _receipt(tmp_path, "sign.json", schema_version=grade.EXTERNAL_EVIDENCE_SCHEMA, head_sha=HEAD, evidence={"signed": True, "notarized": True, "installer_verified": True})
    ci = _receipt(tmp_path, "ci.json", schema_version=grade.EXTERNAL_EVIDENCE_SCHEMA, head_sha=HEAD, evidence={"ci_admitted": True, "ci_passed": True, "jobs_executed": True})
    code = grade.main(["--macos-build-receipt", str(build), "--macos-e2e-receipt", str(e2e), "--signing-receipt", str(signing), "--hosted-ci-receipt", str(ci)])
    return code, captured, captured["payload"]["release_status_line"]


def test_every_gate_passing_is_still_provisional_while_the_ledgers_are_not_certified(tmp_path: Path, monkeypatch) -> None:
    code, captured, line = _run_main(tmp_path, monkeypatch, certified_scope=False)
    assert code == 2
    assert line == "MUCARO AI RELEASE STATUS = PROVISIONAL"
    assert captured["payload"]["blocking_gates"] == ["release_scope"]
    assert set(captured["payload"]["superseded_gates"]) == {"portable_embedded_runtime", "portable_install_reinstall_smoke", "distinct_version_upgrade_rollback", "clean_machine_macos"}


def test_certified_only_when_every_gate_and_the_terminal_rule_hold(tmp_path: Path, monkeypatch) -> None:
    code, captured, line = _run_main(tmp_path, monkeypatch, certified_scope=True)
    assert code == 0 and line == "MUCARO AI RELEASE-GRADE CERTIFIED"
    assert captured["status"] == grade.FINAL_CERTIFIED and captured["payload"]["blocking_gates"] == []


def _checkout_head() -> str | None:
    try:
        return grade.git_head()
    except RuntimeError:
        return None  # not a Git checkout, e.g. an extracted source artifact


def test_final_release_manifest_names_every_missing_or_blocked_entry() -> None:
    root = grade.ROOT
    head = _checkout_head() or "UNKNOWN_HEAD"
    record = build_final_release_manifest(root, head=head)
    assert record["source_revision"] == head
    if _checkout_head():
        assert len(record["source_tree_sha"]) == 40
    else:
        assert record["source_tree_sha"]["state"] == "MISSING"
    assert all(row.get("verified") for row in record["specifications"] if "sha256" in row)
    assert any(row.get("state") == "BLOCKED" for row in record["specifications"])  # BLK-SPEC-001
    assert record["application_artifact"]["state"] == "MISSING"  # no macOS receipt supplied
    assert record["signing_receipt"]["state"] == record["notarization_receipt"]["state"] == "BLOCKED"
    assert "application_artifact" in record["missing_entries"] and record["complete_except_blocked"] is False
    assert {row["path"] for row in record["dependency_locks"]} >= {"packaging/macos/requirements-macos-arm64.lock", "frontend/package-lock.json"}
    assert record["capabilities"]["supported"] and "BLK-SIGN-001" in record["active_blockers"]


def test_final_release_manifest_records_missing_vcs_instead_of_crashing(monkeypatch) -> None:
    from backend.app import release_manifest

    monkeypatch.setattr(release_manifest, "_git", lambda root, *args: None)
    record = build_final_release_manifest(grade.ROOT, head="UNKNOWN_HEAD")
    assert record["source_tree_sha"] == {"state": "MISSING", "reason": "no VCS metadata for this revision"}
    assert record["worktree_clean"] is None
    assert record["complete_except_blocked"] is False


def test_security_profile_comes_from_the_packaged_apps_runtime_report(tmp_path: Path) -> None:
    ready = {"status": "SECURITY_OK", "production_ready": True, "deployment_profile": "production", "cors_origins": ["http://127.0.0.1:5"], "failing": []}

    def gate_for(**fields):
        return grade.security_profile_gate(_receipt(tmp_path, "e2e.json", head_sha=HEAD, **fields), expected_head=HEAD)

    assert grade.security_profile_gate(None, expected_head=HEAD).status == "BLOCKED"
    assert gate_for(security=ready).status == "PASS"
    assert gate_for(security={**ready, "production_ready": False, "status": "SECURITY_WARN", "failing": ["operator_token"]}).status == "FAIL"
    assert gate_for().status == "FAIL"  # a receipt without the runtime report proves nothing
    stale = grade.security_profile_gate(_receipt(tmp_path, "old.json", head_sha="d" * 40, security=ready), expected_head=HEAD)
    assert stale.status == "FAIL"


def test_hosted_ci_accepts_a_merge_commit_only_when_its_tree_equals_the_head(tmp_path: Path) -> None:
    merge = "e" * 40
    evidence = {"ci_admitted": True, "ci_passed": True, "jobs_executed": True}

    def gate_for(**fields):
        payload = {"schema_version": grade.EXTERNAL_EVIDENCE_SCHEMA, "evidence": evidence, **fields}
        return grade.hosted_ci_gate(_receipt(tmp_path, "ci.json", **payload), expected_head=HEAD)

    assert gate_for(head_sha=HEAD).status == "PASS"
    assert gate_for(head_sha=merge, pull_request_head_sha=HEAD, tree_equivalent_to_pull_request_head=True).status == "PASS"
    assert gate_for(head_sha=merge, pull_request_head_sha=HEAD, tree_equivalent_to_pull_request_head=False).status == "FAIL"
    assert gate_for(head_sha=merge, pull_request_head_sha="d" * 40, tree_equivalent_to_pull_request_head=True).status == "FAIL"
    assert gate_for(head_sha=merge).status == "FAIL"
    assert grade.hosted_ci_gate(None, expected_head=HEAD).status == "BLOCKED"
