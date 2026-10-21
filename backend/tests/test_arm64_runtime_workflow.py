from pathlib import Path


def test_arm64_materialization_workflow_runs_on_candidate_and_main() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github" / "workflows" / "arm64-runtime-materialize-cert.yml").read_text(
        encoding="utf-8"
    )

    assert "push:" in workflow
    assert "branches:\n      - main" in workflow
    assert "pull_request:" in workflow
    assert "workflow_dispatch:" in workflow
    for path in (
        "packaging/runtime/macos-arm64.json",
        "packaging/runtime/requirements-macos-arm64.lock",
        "backend/app/portable_runtime_source.py",
        ".github/workflows/arm64-runtime-materialize-cert.yml",
    ):
        assert workflow.count(path) >= 2
