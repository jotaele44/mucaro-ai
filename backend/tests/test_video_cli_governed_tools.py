from __future__ import annotations

from pathlib import Path

import pytest

import backend.app.tools as tools_module
from backend.app.config import Settings
from backend.app.tools import ToolRuntimeError, approve_tool_call, request_tool_call, run_tool_call


def make_settings(root: Path) -> Settings:
    cfg = Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "test.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )
    cfg.ensure_dirs()
    return cfg


def test_governed_regression_run_requires_approval_and_preserves_terminal_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = make_settings(tmp_path)
    captured: dict[str, object] = {}

    def fake_execute(repo_root, *, image, checks, cfg):
        captured["repo_root"] = repo_root
        captured["image"] = image
        captured["checks"] = [(row.check_id, row.kind, row.command, row.required) for row in checks]
        return {
            "status": "CODING_CHECK_SUITE_EXECUTED_PASS",
            "execution_status": "PASS",
            "check_count": len(checks),
            "state_counts": {"PASS": len(checks), "FAIL": 0, "BLOCKED": 0},
            "required_failure_count": 0,
            "required_failures": [],
            "required_blocked_count": 0,
            "required_blocked": [],
            "checks": [
                {"check_id": row.check_id, "kind": row.kind, "required": row.required, "state": "PASS"}
                for row in checks
            ],
            "receipt_sha256": "a" * 64,
        }

    monkeypatch.setattr(tools_module, "execute_coding_check_suite", fake_execute)
    arguments = {
        "repo_root": str(tmp_path / "repo"),
        "image": "sha256:" + ("b" * 64),
        "checks": [
            {"check_id": "unit", "kind": "test", "command": ["pytest", "-q"], "required": True},
            {"check_id": "lint", "kind": "lint", "command": ["ruff", "check", "."], "required": False},
        ],
    }
    requested = request_tool_call("coding.regression_run", arguments, cfg=cfg, requested_by="test")
    call_id = requested["tool_call"]["tool_call_id"]

    with pytest.raises(ToolRuntimeError) as blocked:
        run_tool_call(call_id, cfg=cfg, executed_by="test")
    assert blocked.value.status == "ERROR_TOOL_APPROVAL_REQUIRED"

    approve_tool_call(call_id, cfg=cfg, approved_by="test")
    result = run_tool_call(call_id, cfg=cfg, executed_by="test")["result"]

    assert result["status"] == "CODING_CHECK_SUITE_EXECUTED_PASS"
    assert result["state_counts"] == {"PASS": 2, "FAIL": 0, "BLOCKED": 0}
    assert captured["checks"] == [
        ("unit", "test", ("pytest", "-q"), True),
        ("lint", "lint", ("ruff", "check", "."), False),
    ]


def test_governed_regression_run_rejects_unbounded_check_batches(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    arguments = {
        "repo_root": str(tmp_path / "repo"),
        "image": "sha256:" + ("b" * 64),
        "checks": [
            {"check_id": f"c{index}", "kind": "test", "command": ["pytest", "-q"]}
            for index in range(7)
        ],
    }
    requested = request_tool_call("coding.regression_run", arguments, cfg=cfg, requested_by="test")
    call_id = requested["tool_call"]["tool_call_id"]
    approve_tool_call(call_id, cfg=cfg, approved_by="test")

    with pytest.raises(ToolRuntimeError) as blocked:
        run_tool_call(call_id, cfg=cfg, executed_by="test")
    assert blocked.value.status == "ERROR_CODING_REGRESSION_CHECKS"


def test_governed_github_ci_status_requires_approval_and_preserves_head_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = make_settings(tmp_path)
    captured: dict[str, object] = {}

    def fake_status(**kwargs):
        captured.update(kwargs)
        return {
            "status": "GITHUB_CI_STATUS_OK",
            "repo": kwargs["repo"],
            "branch": kwargs["branch"],
            "workflow": kwargs["workflow"],
            "expected_head_sha": kwargs["expected_head_sha"],
            "ci_state": "PASS",
            "head_matches": True,
            "run_count": 1,
            "runs": [{"database_id": 1, "head_sha": kwargs["expected_head_sha"], "status": "completed", "conclusion": "success"}],
        }

    monkeypatch.setattr(tools_module, "read_github_ci_status", fake_status)
    head = "c" * 40
    arguments = {
        "repo": "jotaele44/mucaro-ai",
        "branch": "main",
        "workflow": "CI",
        "expected_head_sha": head,
        "limit": 3,
    }
    requested = request_tool_call("github.ci_status", arguments, cfg=cfg, requested_by="test")
    call_id = requested["tool_call"]["tool_call_id"]

    with pytest.raises(ToolRuntimeError) as blocked:
        run_tool_call(call_id, cfg=cfg, executed_by="test")
    assert blocked.value.status == "ERROR_TOOL_APPROVAL_REQUIRED"

    approve_tool_call(call_id, cfg=cfg, approved_by="test")
    result = run_tool_call(call_id, cfg=cfg, executed_by="test")["result"]
    assert result["ci_state"] == "PASS"
    assert result["head_matches"] is True
    assert captured == {
        "repo": "jotaele44/mucaro-ai",
        "branch": "main",
        "workflow": "CI",
        "expected_head_sha": head,
        "limit": 3,
    }

def test_branch_create_and_commit_tools_require_approval_and_bind_exact_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = make_settings(tmp_path)
    branch_calls: list[dict[str, object]] = []
    commit_calls: list[dict[str, object]] = []

    def fake_branch(repo_root, *, branch_name, expected_head_sha, cfg):
        branch_calls.append(
            {
                "repo_root": repo_root,
                "branch_name": branch_name,
                "expected_head_sha": expected_head_sha,
            }
        )
        return {
            "status": "CODING_BRANCH_CREATED",
            "branch": branch_name,
            "head_sha": expected_head_sha,
            "receipt_sha256": "a" * 64,
        }

    def fake_commit(repo_root, *, expected_head_sha, expected_branch, approved_paths, message, cfg):
        commit_calls.append(
            {
                "repo_root": repo_root,
                "expected_head_sha": expected_head_sha,
                "expected_branch": expected_branch,
                "approved_paths": list(approved_paths),
                "message": message,
            }
        )
        return {
            "status": "CODING_COMMIT_CREATED",
            "parent_sha": expected_head_sha,
            "commit_sha": "b" * 40,
            "approved_paths": sorted(approved_paths),
            "receipt_sha256": "c" * 64,
        }

    monkeypatch.setattr(tools_module, "create_work_branch_transaction", fake_branch)
    monkeypatch.setattr(tools_module, "commit_approved_changes", fake_commit)

    branch_args = {
        "repo_root": str(tmp_path / "repo"),
        "branch_name": "feature/video-cli",
        "expected_head_sha": "1" * 40,
    }
    requested = request_tool_call("coding.branch_create", branch_args, cfg=cfg, requested_by="test")
    branch_call_id = requested["tool_call"]["tool_call_id"]
    with pytest.raises(ToolRuntimeError) as blocked:
        run_tool_call(branch_call_id, cfg=cfg, executed_by="test")
    assert blocked.value.status == "ERROR_TOOL_APPROVAL_REQUIRED"
    approve_tool_call(branch_call_id, cfg=cfg, approved_by="test")
    branch_result = run_tool_call(branch_call_id, cfg=cfg, executed_by="test")["result"]
    assert branch_result["status"] == "CODING_BRANCH_CREATED"

    commit_args = {
        "repo_root": str(tmp_path / "repo"),
        "expected_head_sha": "1" * 40,
        "expected_branch": "feature/video-cli",
        "approved_paths": ["backend/app/a.py", "backend/tests/test_a.py"],
        "message": "Apply bounded change",
    }
    requested = request_tool_call("coding.commit", commit_args, cfg=cfg, requested_by="test")
    commit_call_id = requested["tool_call"]["tool_call_id"]
    with pytest.raises(ToolRuntimeError) as blocked:
        run_tool_call(commit_call_id, cfg=cfg, executed_by="test")
    assert blocked.value.status == "ERROR_TOOL_APPROVAL_REQUIRED"
    approve_tool_call(commit_call_id, cfg=cfg, approved_by="test")
    commit_result = run_tool_call(commit_call_id, cfg=cfg, executed_by="test")["result"]
    assert commit_result["status"] == "CODING_COMMIT_CREATED"

    assert branch_calls == [branch_args]
    assert commit_calls == [commit_args]


@pytest.mark.parametrize(
    ("tool_name", "arguments", "status"),
    [
        (
            "coding.branch_create",
            {"repo_root": "/repo", "branch_name": "", "expected_head_sha": "1" * 40},
            "ERROR_CODING_BRANCH_REQUIRED",
        ),
        (
            "coding.branch_create",
            {"repo_root": "/repo", "branch_name": "feature/x", "expected_head_sha": ""},
            "ERROR_CODING_BRANCH_HEAD_REQUIRED",
        ),
        (
            "coding.commit",
            {
                "repo_root": "/repo",
                "expected_head_sha": "1" * 40,
                "expected_branch": "feature/x",
                "approved_paths": [],
                "message": "x",
            },
            "ERROR_CODING_COMMIT_PATHS_REQUIRED",
        ),
        (
            "coding.commit",
            {
                "repo_root": "/repo",
                "expected_head_sha": "1" * 40,
                "expected_branch": "feature/x",
                "approved_paths": ["a.py"],
                "message": "",
            },
            "ERROR_CODING_COMMIT_MESSAGE_REQUIRED",
        ),
    ],
)
def test_git_lifecycle_tool_argument_negative_controls(
    tmp_path: Path,
    tool_name: str,
    arguments: dict,
    status: str,
) -> None:
    cfg = make_settings(tmp_path)
    requested = request_tool_call(tool_name, arguments, cfg=cfg, requested_by="test")
    call_id = requested["tool_call"]["tool_call_id"]
    approve_tool_call(call_id, cfg=cfg, approved_by="test")

    with pytest.raises(ToolRuntimeError) as blocked:
        run_tool_call(call_id, cfg=cfg, executed_by="test")
    assert blocked.value.status == status

