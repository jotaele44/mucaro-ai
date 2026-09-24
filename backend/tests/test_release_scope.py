from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from backend.app import cli
from backend.app.release_scope import (
    LEDGER_FILES,
    RELEASE_DIR,
    STATUS_CERTIFIED,
    STATUS_INVALID,
    STATUS_PROVISIONAL,
    capture_state_snapshot,
    compute_denominator,
    compute_release_status,
    freeze_release_scope,
    validate_release_scope,
    write_state_snapshot,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(root: Path, kind: str) -> dict:
    return json.loads((root / RELEASE_DIR / LEDGER_FILES[kind][0]).read_text(encoding="utf-8"))


def _save(root: Path, kind: str, payload: dict) -> None:
    (root / RELEASE_DIR / LEDGER_FILES[kind][0]).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _clone_release_root(tmp_path: Path) -> Path:
    """Copy the committed release control plane plus every evidence path it cites."""
    root = tmp_path / "repo"
    for relative in ("docs/release", "schemas", str(RELEASE_DIR)):
        shutil.copytree(REPO_ROOT / relative, root / relative)
    ledger = _load(REPO_ROOT, "acceptance")
    for row in ledger["rows"]:
        for ref in [*row["evidence_refs"], row["positive_gate"], row["negative_gate"]]:
            if not ref:
                continue
            path_part = ref.split("::", 1)[0]
            source = REPO_ROOT / path_part
            target = root / path_part
            if source.is_file() and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            elif source.is_dir() and not target.exists():
                shutil.copytree(source, target)
    return root


def _codes(report: dict) -> set[str]:
    return {error["code"] for error in report["errors"]}


def _minimal_certifiable_root(tmp_path: Path) -> Path:
    root = tmp_path / "mini"
    (root / "schemas").mkdir(parents=True)
    for _, schema_name in LEDGER_FILES.values():
        shutil.copyfile(REPO_ROOT / "schemas" / schema_name, root / "schemas" / schema_name)
    spec = root / "docs" / "spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("REQ-001 — First\nREQ-002 — Second\n", encoding="utf-8")
    evidence = root / "tests" / "test_mini.py"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("def test_ok():\n    assert True\n\ndef test_bad():\n    assert True\n", encoding="utf-8")
    (root / RELEASE_DIR).mkdir(parents=True)
    manifest = {
        "schema_version": "mucaro-release-scope-manifest/1.0",
        "manifest_id": "MINI",
        "release_class": {"name": "mini", "platform": "macOS", "architectures": ["arm64"], "minimum_os": "13.0", "distribution": "zip"},
        "bound_specifications": [
            {
                "spec_id": "MINI_SPEC",
                "path": "docs/spec.md",
                "sha256": hashlib.sha256(spec.read_bytes()).hexdigest(),
                "id_pattern": r"^(?P<id>REQ-[0-9]{3}) — (?P<title>.+)$",
                "id_prefix": "",
                "binding": "BOUND",
            }
        ],
        "missing_specifications": [],
        "requirements": [
            {"requirement_id": rid, "title": title, "source": "MINI_SPEC", "requirement_class": "PRODUCT",
             "scope_state": "IN_SCOPE", "verification_method": "AUTOMATED_TEST"}
            for rid, title in (("REQ-001", "First"), ("REQ-002", "Second"))
        ],
    }
    rows = [
        {"requirement_id": rid, "requirement_text": title, "scope_state": "IN_SCOPE", "implementation_state": "IMPLEMENTED",
         "implementation_refs": [], "dependencies": [], "positive_gate": "tests/test_mini.py::test_ok",
         "negative_gate": "tests/test_mini.py::test_bad", "benchmark": None, "evidence_refs": ["tests/test_mini.py"],
         "state": "PASS", "blocker_id": None, "dag_node": "N00", "notes": ""}
        for rid, title in (("REQ-001", "First"), ("REQ-002", "Second"))
    ]
    _save(root, "manifest", manifest)
    _save(root, "acceptance", {"schema_version": "mucaro-release-acceptance-ledger/1.0", "rows": rows})
    _save(root, "dag", {"schema_version": "mucaro-release-dag/1.0", "nodes": [
        {"node_id": "N00", "title": "all", "requirement_ids": ["REQ-001", "REQ-002"], "dependencies": [], "state": "PASS",
         "risk": "LOW", "user_value": "LOW", "downstream_unlock_value": "LOW", "expected_rework_cost": "LOW",
         "evidence_required": [], "blocker": None}
    ]})
    _save(root, "blockers", {"schema_version": "mucaro-release-blocker-ledger/1.0", "blockers": []})
    _save(root, "contradictions", {"schema_version": "mucaro-release-contradiction-ledger/1.0", "contradictions": []})
    _save(root, "scope_deltas", {"schema_version": "mucaro-release-scope-delta-ledger/1.0", "deltas": []})
    return root


def test_committed_release_scope_is_valid():
    report = validate_release_scope(REPO_ROOT)
    assert report["status"] == "RELEASE_SCOPE_VALID", report["errors"]
    assert report["enumerated_requirement_count"] >= 129
    assert report["denominator"]["arithmetic_closes"] is True


def test_silent_scope_loss_is_detected(tmp_path):
    root = _clone_release_root(tmp_path)
    manifest = _load(root, "manifest")
    manifest["requirements"] = [row for row in manifest["requirements"] if row["requirement_id"] != "TGT-022"]
    _save(root, "manifest", manifest)
    report = validate_release_scope(root, compare_frozen=False)
    assert report["status"] == "RELEASE_SCOPE_INVALID"
    assert {"code": "ERROR_SILENT_SCOPE_LOSS", "detail": "TGT-022"} in report["errors"]


def test_duplicate_requirement_is_rejected(tmp_path):
    root = _clone_release_root(tmp_path)
    manifest = _load(root, "manifest")
    manifest["requirements"].append(copy.deepcopy(manifest["requirements"][0]))
    _save(root, "manifest", manifest)
    report = validate_release_scope(root, compare_frozen=False)
    assert "ERROR_DUPLICATE_REQUIREMENT" in _codes(report)


def test_not_applicable_requires_justification(tmp_path):
    root = _clone_release_root(tmp_path)
    manifest = _load(root, "manifest")
    ledger = _load(root, "acceptance")
    for row in manifest["requirements"]:
        if row["requirement_id"] == "TGT-020":
            row["scope_state"] = "NOT_APPLICABLE"
    for row in ledger["rows"]:
        if row["requirement_id"] == "TGT-020":
            row["scope_state"] = "NOT_APPLICABLE"
            row["state"] = "NOT_APPLICABLE"
    _save(root, "manifest", manifest)
    _save(root, "acceptance", ledger)
    report = validate_release_scope(root, compare_frozen=False)
    assert {"code": "ERROR_NOT_APPLICABLE_UNJUSTIFIED", "detail": "TGT-020"} in report["errors"]


def test_pass_without_evidence_is_rejected(tmp_path):
    root = _clone_release_root(tmp_path)
    ledger = _load(root, "acceptance")
    for row in ledger["rows"]:
        if row["requirement_id"] == "TGT-004":
            row["state"] = "PASS"
    _save(root, "acceptance", ledger)
    report = validate_release_scope(root, compare_frozen=False)
    assert {"code": "ERROR_PASS_WITHOUT_EVIDENCE", "detail": "TGT-004"} in report["errors"]


def test_fabricated_evidence_reference_is_rejected(tmp_path):
    root = _clone_release_root(tmp_path)
    ledger = _load(root, "acceptance")
    for row in ledger["rows"]:
        if row["requirement_id"] == "TGT-004":
            row["state"] = "PASS"
            row["positive_gate"] = "backend/tests/test_release_scope.py::test_that_was_never_written"
            row["negative_gate"] = "backend/tests/test_release_scope.py::test_silent_scope_loss_is_detected"
            row["evidence_refs"] = ["data/exports/receipt_that_does_not_exist.json"]
    _save(root, "acceptance", ledger)
    report = validate_release_scope(root, compare_frozen=False)
    codes = _codes(report)
    assert "ERROR_EVIDENCE_TEST_MISSING" in codes
    assert "ERROR_EVIDENCE_REF_MISSING" in codes


def test_spec_hash_mismatch_is_detected(tmp_path):
    root = _clone_release_root(tmp_path)
    spec = root / "docs" / "release" / "MUCARO_RELEASE_ENGINEERING_CONTRACT_V1.md"
    spec.write_text(spec.read_text(encoding="utf-8") + "\nTGT-999 — Injected requirement\n", encoding="utf-8")
    report = validate_release_scope(root, compare_frozen=False)
    codes = _codes(report)
    assert "ERROR_SPEC_HASH_MISMATCH" in codes
    assert {"code": "ERROR_SILENT_SCOPE_LOSS", "detail": "TGT-999"} in report["errors"]


def test_dag_cycle_is_detected(tmp_path):
    root = _clone_release_root(tmp_path)
    dag = _load(root, "dag")
    nodes = {node["node_id"]: node for node in dag["nodes"]}
    nodes["N00"]["dependencies"] = ["N25"]
    _save(root, "dag", dag)
    report = validate_release_scope(root, compare_frozen=False)
    assert "ERROR_DAG_CYCLE" in _codes(report)


def test_blocked_state_requires_active_blocker(tmp_path):
    root = _clone_release_root(tmp_path)
    ledger = _load(root, "acceptance")
    for row in ledger["rows"]:
        if row["requirement_id"] == "TGT-008":
            row["state"] = "BLOCKED"
            row["blocker_id"] = None
        if row["requirement_id"] == "TGT-009":
            row["state"] = "BLOCKED"
            row["blocker_id"] = "BLK-NOPE-001"
    _save(root, "acceptance", ledger)
    blockers = _load(root, "blockers")
    for blocker in blockers["blockers"]:
        if blocker["blocker_id"] == "BLK-SPEC-001":
            blocker["status"] = "RESOLVED"
            blocker["resolution"] = "test"
    _save(root, "blockers", blockers)
    report = validate_release_scope(root, compare_frozen=False)
    errors = report["errors"]
    assert {"code": "ERROR_BLOCKED_WITHOUT_BLOCKER", "detail": "TGT-008"} in errors
    assert {"code": "ERROR_UNKNOWN_BLOCKER", "detail": "TGT-009:BLK-NOPE-001"} in errors
    assert {"code": "ERROR_BLOCKER_NOT_ACTIVE", "detail": "KERNEL-003:BLK-SPEC-001"} in errors


def test_scope_delta_defect_must_enter_denominator(tmp_path):
    root = _clone_release_root(tmp_path)
    deltas = _load(root, "scope_deltas")
    deltas["deltas"].append(
        {"delta_id": "SD-900", "classification": "DEFECT_REVEALED", "title": "hidden defect", "discovered_by": "test",
         "evidence": ["test"], "adopted": False, "adoption_authority": None, "parent_requirement_ids": ["TGT-021"]}
    )
    _save(root, "scope_deltas", deltas)
    report = validate_release_scope(root, compare_frozen=False)
    assert {"code": "ERROR_SCOPE_DELTA_NOT_IN_DENOMINATOR", "detail": "SD-900"} in report["errors"]


def test_unadopted_enhancement_cannot_enter_denominator(tmp_path):
    root = _clone_release_root(tmp_path)
    manifest = _load(root, "manifest")
    ledger = _load(root, "acceptance")
    manifest["requirements"].append(
        {"requirement_id": "SD-009", "title": "App Store lane", "source": "SCOPE_DELTA", "requirement_class": "PRODUCT",
         "scope_state": "IN_SCOPE", "verification_method": "EXTERNAL_RECEIPT"}
    )
    ledger["rows"].append(
        {"requirement_id": "SD-009", "requirement_text": "App Store lane", "scope_state": "IN_SCOPE",
         "implementation_state": "PARTIAL", "implementation_refs": [], "dependencies": [], "positive_gate": None,
         "negative_gate": None, "benchmark": None, "evidence_refs": [], "state": "OPEN", "blocker_id": None,
         "dag_node": None, "notes": ""}
    )
    _save(root, "manifest", manifest)
    _save(root, "acceptance", ledger)
    report = validate_release_scope(root, compare_frozen=False)
    assert {"code": "ERROR_ENHANCEMENT_IN_DENOMINATOR", "detail": "SD-009"} in report["errors"]


def test_ledger_drift_is_detected(tmp_path):
    root = _clone_release_root(tmp_path)
    ledger = _load(root, "acceptance")
    ledger["rows"][0]["requirement_text"] = "silently reworded"
    ledger["rows"] = ledger["rows"][:-1]
    _save(root, "acceptance", ledger)
    report = validate_release_scope(root, compare_frozen=False)
    codes = _codes(report)
    assert "ERROR_LEDGER_DRIFT" in codes
    assert "ERROR_LEDGER_ROW_MISSING" in codes


def test_state_outside_denominator_is_rejected(tmp_path):
    root = _clone_release_root(tmp_path)
    ledger = _load(root, "acceptance")
    ledger["rows"][5]["state"] = "DEFERRED"
    _save(root, "acceptance", ledger)
    report = validate_release_scope(root, compare_frozen=False)
    assert report["status"] == "RELEASE_SCOPE_INVALID"
    assert "ERROR_SCOPE_SCHEMA_INVALID" in _codes(report)


def test_denominator_arithmetic_closes():
    ledger = _load(REPO_ROOT, "acceptance")
    denominator = compute_denominator(ledger)
    assert denominator["total"] == len(ledger["rows"])
    assert sum(denominator["by_state"].values()) == denominator["total"]
    assert sum(denominator["by_scope"].values()) == denominator["total"]
    assert denominator["arithmetic_closes"] is True


def test_frozen_checkpoint_detects_dropped_records(tmp_path):
    root = _clone_release_root(tmp_path)
    shutil.rmtree(root / RELEASE_DIR / "frozen", ignore_errors=True)
    freeze_release_scope(root, "20260101T000000Z-test")
    contradictions = _load(root, "contradictions")
    dropped = contradictions["contradictions"].pop()
    _save(root, "contradictions", contradictions)
    blockers = _load(root, "blockers")
    dropped_blocker = blockers["blockers"].pop()
    _save(root, "blockers", blockers)
    report = validate_release_scope(root)
    assert report["frozen_checkpoint"] == "20260101T000000Z-test"
    errors = report["errors"]
    assert {"code": "ERROR_FROZEN_CONTRADICTION_DROPPED", "detail": f"20260101T000000Z-test:{dropped['contradiction_id']}"} in errors
    assert {"code": "ERROR_FROZEN_BLOCKER_DROPPED", "detail": f"20260101T000000Z-test:{dropped_blocker['blocker_id']}"} in errors


def test_materiality_cannot_be_redefined_after_freeze(tmp_path):
    root = _clone_release_root(tmp_path)
    shutil.rmtree(root / RELEASE_DIR / "frozen", ignore_errors=True)
    freeze_release_scope(root, "20260101T000000Z-test")
    contradictions = _load(root, "contradictions")
    material = next(row for row in contradictions["contradictions"] if row["material"])
    material["material"] = False
    _save(root, "contradictions", contradictions)
    report = validate_release_scope(root)
    assert "ERROR_MATERIALITY_REDEFINED" in _codes(report)


def test_freeze_rejects_invalid_scope_and_bad_labels(tmp_path):
    root = _clone_release_root(tmp_path)
    with pytest.raises(ValueError):
        freeze_release_scope(root, "not-a-label")
    manifest = _load(root, "manifest")
    manifest["requirements"] = manifest["requirements"][1:]
    _save(root, "manifest", manifest)
    with pytest.raises(ValueError):
        freeze_release_scope(root, "20260101T000000Z-bad")


def test_release_status_is_provisional_with_residue():
    status = compute_release_status(REPO_ROOT)
    assert status["status"] == STATUS_PROVISIONAL
    assert status["release_status_line"] == "MUCARO AI RELEASE STATUS = PROVISIONAL"
    assert status["conditions"]["bound_specification"] is False
    assert "TGT-027" in status["residue"]["externally_blocked"]
    ledger = json.loads((REPO_ROOT / "_architecture/release/contradiction_ledger_v1.json").read_text(encoding="utf-8"))
    expected = sorted(row["contradiction_id"] for row in ledger["contradictions"] if row["material"] and row["state"] == "UNRESOLVED")
    assert status["residue"]["unresolved_material_contradictions"] == expected
    assert "BLK-SIGN-001" in status["residue"]["active_blockers"]
    assert status["residue"]["open"]


def test_release_status_certifies_only_when_every_condition_holds(tmp_path):
    root = _minimal_certifiable_root(tmp_path)
    status = compute_release_status(root)
    assert status["status"] == STATUS_CERTIFIED, status
    assert all(status["conditions"].values())

    ledger = _load(root, "acceptance")
    ledger["rows"][1]["state"] = "PROVISIONAL"
    _save(root, "acceptance", ledger)
    assert compute_release_status(root)["status"] == STATUS_PROVISIONAL

    ledger["rows"][1]["state"] = "PASS"
    _save(root, "acceptance", ledger)
    contradictions = _load(root, "contradictions")
    contradictions["contradictions"].append(
        {"contradiction_id": "RC-001", "type": "TEST", "observation_a": {"claim": "a", "evidence": "a"},
         "observation_b": {"claim": "b", "evidence": "b"}, "adjudication": "", "state": "UNRESOLVED",
         "material": True, "materiality_reason": "affects a claimed feature"}
    )
    _save(root, "contradictions", contradictions)
    status = compute_release_status(root)
    assert status["status"] == STATUS_PROVISIONAL
    assert status["residue"]["unresolved_material_contradictions"] == ["RC-001"]

    (root / "docs" / "spec.md").write_text("REQ-001 — First\n", encoding="utf-8")
    assert compute_release_status(root)["status"] == STATUS_INVALID


def test_snapshot_records_credential_presence_not_values(tmp_path):
    snapshot = capture_state_snapshot(
        REPO_ROOT,
        tests={"recorded": True, "backend_pytest": "1 passed"},
        environ={"ANTHROPIC_API_KEY": "sk-secret-value-123", "MUCARO_BRAVE_SEARCH_API_KEY": ""},
    )
    presence = {row["name"]: row["present"] for row in snapshot["credentials"]}
    assert presence["ANTHROPIC_API_KEY"] is True
    assert presence["MUCARO_BRAVE_SEARCH_API_KEY"] is False
    assert snapshot["repository"]["head_sha"]
    path = write_state_snapshot(REPO_ROOT, snapshot, tmp_path / "snapshot.json")
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == "mucaro-release-state-snapshot/1.0"


def test_snapshot_never_contains_secret_values(tmp_path):
    secret = "sk-" + "ant-never-write-me-0123456789"  # built at runtime so the source holds no key-shaped literal
    snapshot = capture_state_snapshot(REPO_ROOT, environ={"ANTHROPIC_API_KEY": secret, "GITHUB_TOKEN": secret})
    path = write_state_snapshot(REPO_ROOT, snapshot, tmp_path / "snapshot.json")
    assert secret not in path.read_text(encoding="utf-8")
    committed = (REPO_ROOT / RELEASE_DIR / "current_state_snapshot_v1.json").read_text(encoding="utf-8")
    assert "sk-" not in committed


def test_cli_release_scope_validate_and_status(capsys):
    assert cli.main(["release", "scope", "validate", "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "RELEASE_SCOPE_VALID"
    assert cli.main(["release", "scope", "status", "--format", "text"]) == 0
    text = capsys.readouterr().out
    assert "MUCARO AI RELEASE STATUS = PROVISIONAL" in text
    assert "arithmetic_closes=True" in text


def test_release_status_doc_block_is_generated_from_computed_state():
    from backend.app.release_scope import compute_release_status, render_status_block, status_doc_block

    root = Path(__file__).resolve().parents[2]
    expected = render_status_block(compute_release_status(root))
    assert status_doc_block(root) == expected, "run: mucaro release scope status --write-doc"
    assert "RELEASE-GRADE CERTIFIED" not in expected or "zero active blockers | yes" in expected


def test_write_status_doc_inserts_then_replaces_only_the_block(tmp_path):
    from backend.app.release_scope import STATUS_BLOCK_BEGIN, STATUS_DOC, write_status_doc

    doc = tmp_path / STATUS_DOC
    doc.parent.mkdir(parents=True)
    doc.write_text("# Release Status\n\nHand-written history.\n", encoding="utf-8")
    status = {"release_status_line": "MUCARO AI RELEASE STATUS = PROVISIONAL", "denominator": {"total": 2, "by_state": {"PASS": 1, "OPEN": 1}, "arithmetic_closes": True},
              "conditions": {"zero_in_scope_open": False}, "residue": {"open": ["X-1"]}}
    write_status_doc(tmp_path, status)
    first = doc.read_text(encoding="utf-8")
    assert first.startswith("# Release Status\n\n" + STATUS_BLOCK_BEGIN) and first.rstrip().endswith("Hand-written history.")
    write_status_doc(tmp_path, {**status, "residue": {"open": []}})
    second = doc.read_text(encoding="utf-8")
    assert second.count(STATUS_BLOCK_BEGIN) == 1 and "Hand-written history." in second and "`X-1`" not in second
