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
    state, reason = shadow_qa.classify_rendered(
        ["frontend/src/App.jsx"], checkpoint_sha="a" * 40, receipt_paths=[]
    )

    assert state == "BLOCKED"
    assert "exact-checkpoint browser receipts" in reason


def test_non_frontend_checkpoint_does_not_invent_rendered_pass():
    state, reason = shadow_qa.classify_rendered(
        ["backend/app/response_flow.py"], checkpoint_sha="a" * 40, receipt_paths=[]
    )

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



def test_overall_state_cannot_pass_when_rendered_evidence_is_blocked():
    state = shadow_qa.overall_state_for_results(
        failed_gates=[],
        blocked_gates=[],
        unresolved_vectors=[],
        blocked_vectors=[],
        rendered_state="BLOCKED",
    )

    assert state == "PROVISIONAL"


def test_overall_state_passes_when_all_required_evidence_is_closed():
    state = shadow_qa.overall_state_for_results(
        failed_gates=[],
        blocked_gates=[],
        unresolved_vectors=[],
        blocked_vectors=[],
        rendered_state="AUDIT_ONLY",
    )

    assert state == "PASS"


def test_rendered_receipts_require_exact_checkpoint_both_browsers(tmp_path: Path) -> None:
    checkpoint = "a" * 40
    receipts = []
    for browser in ("chromium 141", "webkit 26"):
        path = tmp_path / f"{browser.split()[0]}.json"
        path.write_text(
            __import__("json").dumps(
                {
                    "schema_version": "mucaro-rendered-qa/1.0",
                    "status": "RENDERED_QA_PASS",
                    "head_sha": checkpoint,
                    "worktree_clean": True,
                    "failed": 0,
                    "browser": browser,
                }
            ),
            encoding="utf-8",
        )
        receipts.append(path)

    state, reason = shadow_qa.classify_rendered(
        ["frontend/src/App.jsx"], checkpoint_sha=checkpoint, receipt_paths=receipts
    )
    assert state == "PASS"
    assert "Chromium and WebKit" in reason


def test_execute_vector_evidence_generates_exact_checkpoint_receipt(monkeypatch) -> None:
    class Completed:
        returncode = 0
        stdout = "1 passed"
        stderr = ""

    monkeypatch.setattr(shadow_qa.subprocess, "run", lambda *args, **kwargs: Completed())
    vectors = {
        "stale": {
            "state": "PASS",
            "evidence": [
                {
                    "kind": "CHECKPOINT_TEST",
                    "path": "backend/tests/test_example.py",
                    "selector": "test_stale",
                }
            ],
        }
    }
    failures = shadow_qa.execute_vector_evidence(vectors, checkpoint_sha="b" * 40)
    assert failures == []
    receipt = vectors["stale"]["runtime_execution_receipt"]
    assert receipt["schema_version"] == "mucaro-shadow-execution-receipt/1.0"
    assert receipt["checkpoint_sha"] == "b" * 40
    assert receipt["status"] == "PASS"

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
