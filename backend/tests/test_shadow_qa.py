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
