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
