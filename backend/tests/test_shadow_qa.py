from pathlib import Path
import subprocess
import sys

from scripts import shadow_qa


def test_adjacent_paths_expands_backend_frontend_schema_and_workflow_surfaces():
    paths = [
        "backend/app/response_flow.py",
        "frontend/src/App.jsx",
        "schemas/answer_trace.schema.json",
        ".github/workflows/ci.yml",
    ]

    adjacent = shadow_qa.adjacent_paths(paths)

    assert "backend/tests" in adjacent
    assert "backend/app/main.py" in adjacent
    assert "backend/app/cli.py" in adjacent
    assert "frontend/src" in adjacent
    assert "scripts" in adjacent
    assert "docs" in adjacent


def test_rendered_frontend_changes_fail_closed_without_browser_evidence():
    state, reason = shadow_qa.classify_rendered(["frontend/src/App.jsx"])

    assert state == "BLOCKED"
    assert "browser/device" in reason


def test_non_frontend_checkpoint_does_not_invent_rendered_pass():
    state, reason = shadow_qa.classify_rendered(["backend/app/response_flow.py"])

    assert state == "AUDIT_ONLY"
    assert "No frontend surface changed" in reason


def test_shadow_bootstrap_vectors_are_explicitly_not_applicable():
    paths = sorted(shadow_qa.SHADOW_BOOTSTRAP_PATHS)

    results = shadow_qa.adversarial_vector_results(paths)

    assert set(results) == set(shadow_qa.REQUIRED_ADVERSARIAL_VECTORS)
    assert all(result["state"] == "NOT_APPLICABLE" for result in results.values())
    assert all("Shadow QA infrastructure" in result["reason"] for result in results.values())


def test_non_bootstrap_runtime_change_cannot_silently_pass_adversarial_vectors():
    results = shadow_qa.adversarial_vector_results(["backend/app/response_flow.py"])

    assert set(results) == set(shadow_qa.REQUIRED_ADVERSARIAL_VECTORS)
    assert all(result["state"] == "UNRESOLVED" for result in results.values())
    assert all("checkpoint-specific adversarial evidence" in result["reason"] for result in results.values())


def test_shadow_exit_codes_distinguish_pass_fail_and_provisional():
    assert shadow_qa.exit_code_for_state("PASS") == 0
    assert shadow_qa.exit_code_for_state("FAIL") == 1
    assert shadow_qa.exit_code_for_state("PROVISIONAL") != 0
    assert shadow_qa.exit_code_for_state("BLOCKED") != 0


def test_shadow_workflow_invokes_runner_as_module() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github" / "workflows" / "shadow-qa.yml").read_text(encoding="utf-8")

    assert "python -m scripts.shadow_qa" in workflow
    assert "python scripts/shadow_qa.py" not in workflow

    completed = subprocess.run(
        [sys.executable, "-m", "scripts.shadow_qa", "--help"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Run bounded MUCARO Shadow QA" in completed.stdout


def test_vector_selector_registry_closes_exact_required_denominator() -> None:
    assert set(shadow_qa.VECTOR_TEST_SELECTORS) == set(shadow_qa.REQUIRED_ADVERSARIAL_VECTORS)
    assert len(shadow_qa.VECTOR_TEST_SELECTORS) == len(shadow_qa.REQUIRED_ADVERSARIAL_VECTORS)
    assert all("backend/tests/" in selector and "::test_" in selector for selector in shadow_qa.VECTOR_TEST_SELECTORS.values())


def test_exact_vector_certification_generates_valid_bound_evidence(tmp_path, monkeypatch) -> None:
    base = "a" * 40
    head = "b" * 40
    execution = tmp_path / "data" / "exports" / "shadow" / "execution.json"
    manifest = tmp_path / "data" / "exports" / "shadow" / "manifest.json"

    monkeypatch.setattr(shadow_qa, "ROOT", tmp_path)
    monkeypatch.setattr(shadow_qa.shadow_evidence, "ROOT", tmp_path)

    definitions = "\n".join(
        f"def {selector.split('::')[-1]}():\n    pass"
        for selector in shadow_qa.VECTOR_TEST_SELECTORS.values()
    )
    monkeypatch.setattr(shadow_qa.shadow_evidence, "_git_show_text", lambda checkpoint, path: definitions)
    monkeypatch.setattr(
        shadow_qa,
        "run",
        lambda command, cwd=tmp_path: {"command": command, "cwd": ".", "returncode": 0},
    )

    gate, results, receipt_path, manifest_path = shadow_qa.certify_exact_vectors(
        base_sha=base,
        checkpoint_sha=head,
        broad_gates=[{"command": ["broad"], "cwd": ".", "returncode": 0}],
        execution_receipt_path=execution,
        manifest_path=manifest,
    )

    assert gate["returncode"] == 0
    assert set(results) == set(shadow_qa.REQUIRED_ADVERSARIAL_VECTORS)
    assert all(result["state"] == "PASS" for result in results.values())
    assert receipt_path == "data/exports/shadow/execution.json"
    assert manifest_path == "data/exports/shadow/manifest.json"

    execution_payload = __import__("json").loads(execution.read_text(encoding="utf-8"))
    manifest_payload = __import__("json").loads(manifest.read_text(encoding="utf-8"))
    assert execution_payload["status"] == "PASS"
    assert execution_payload["base_sha"] == base
    assert execution_payload["checkpoint_sha"] == head
    assert execution_payload["vector_selector_count"] == 15
    assert manifest_payload["base_sha"] == base
    assert manifest_payload["checkpoint_sha"] == head
    assert len(manifest_payload["vectors"]) == 15
