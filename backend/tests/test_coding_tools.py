from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

import backend.app.tools as tools_module

from backend.app.coding_patch import validate_patch_proposal
from backend.app.config import Settings
from backend.app.tools import (
    ToolRuntimeError,
    approve_tool_call,
    list_tools,
    request_tool_call,
    run_tool_call,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required for coding tool tests")


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


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def make_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "mucaro-tests@example.invalid")
    git(repo, "config", "user.name", "Mucaro Tests")
    (repo / "tracked.txt").write_text("alpha\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "initial")
    return repo


def patch_for() -> str:
    return (
        "diff --git a/tracked.txt b/tracked.txt\n"
        "--- a/tracked.txt\n"
        "+++ b/tracked.txt\n"
        "@@ -1 +1 @@\n"
        "-alpha\n"
        "+beta\n"
    )


def test_coding_tools_are_allowlisted_but_still_require_approval(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    tools = {tool["name"]: tool for tool in list_tools()["tools"]}
    read_only_names = ("coding.repo_status", "coding.read_file", "coding.diff", "coding.patch_check")

    assert set(read_only_names).issubset(tools)
    assert "coding.patch_apply" in tools
    assert all(tools[name]["requires_approval"] is True for name in (*read_only_names, "coding.patch_apply"))
    assert all(tools[name]["mutates_state"] is False for name in read_only_names)
    assert tools["coding.patch_apply"]["mutates_state"] is True

    requested = request_tool_call(
        "coding.repo_status",
        {"repo_root": str(repo)},
        cfg=cfg,
        requested_by="test",
        reason="coding read-only regression",
    )
    tool_call_id = requested["tool_call"]["tool_call_id"]

    with pytest.raises(ToolRuntimeError) as blocked:
        run_tool_call(tool_call_id, cfg=cfg, executed_by="test")
    assert blocked.value.status == "ERROR_TOOL_APPROVAL_REQUIRED"

    approve_tool_call(tool_call_id, cfg=cfg, approved_by="test")
    executed = run_tool_call(tool_call_id, cfg=cfg, executed_by="test")

    assert executed["status"] == "TOOL_CALL_EXECUTED"
    assert executed["result"]["status"] == "CODING_REPOSITORY_STATUS_OK"
    assert executed["result"]["dirty_status"] == "CLEAN"


def test_coding_read_file_diff_and_patch_check_use_same_approved_boundary(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    patch = patch_for()

    for tool_name, arguments, expected_status in (
        ("coding.read_file", {"repo_root": str(repo), "path": "tracked.txt"}, "CODING_FILE_READ_OK"),
        ("coding.diff", {"repo_root": str(repo), "base": "HEAD"}, "CODING_DIFF_OK"),
        ("coding.patch_check", {"repo_root": str(repo), "patch": patch}, "CODING_PATCH_CHECK_OK"),
    ):
        requested = request_tool_call(tool_name, arguments, cfg=cfg, requested_by="test")
        tool_call_id = requested["tool_call"]["tool_call_id"]
        approve_tool_call(tool_call_id, cfg=cfg, approved_by="test")
        executed = run_tool_call(tool_call_id, cfg=cfg, executed_by="test")
        assert executed["result"]["status"] == expected_status

    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "alpha\n"


def test_patch_apply_requires_approval_and_exact_proposal_receipt(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    patch = patch_for()
    proposal = validate_patch_proposal(repo, patch, cfg=cfg)
    head = git(repo, "rev-parse", "HEAD").strip()
    patch_hash = hashlib.sha256(patch.encode("utf-8")).hexdigest()

    requested = request_tool_call(
        "coding.patch_apply",
        {
            "repo_root": str(repo),
            "patch": patch,
            "expected_head_sha": head,
            "expected_patch_sha256": patch_hash,
            "expected_preimages": proposal["preimages"],
        },
        cfg=cfg,
        requested_by="test",
        reason="approved patch transaction regression",
    )
    tool_call_id = requested["tool_call"]["tool_call_id"]

    with pytest.raises(ToolRuntimeError) as blocked:
        run_tool_call(tool_call_id, cfg=cfg, executed_by="test")
    assert blocked.value.status == "ERROR_TOOL_APPROVAL_REQUIRED"
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "alpha\n"

    approve_tool_call(tool_call_id, cfg=cfg, approved_by="test")
    executed = run_tool_call(tool_call_id, cfg=cfg, executed_by="test")

    assert executed["result"]["status"] == "CODING_MUTATION_APPLIED"
    assert executed["result"]["head_sha"] == head
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "beta\n"

def test_coding_exec_requires_approval_and_preserves_isolated_execution_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    image = "sha256:" + ("a" * 64)
    command = ["python", "-m", "pytest", "-q"]

    tools = {tool["name"]: tool for tool in list_tools()["tools"]}
    assert tools["coding.exec"]["requires_approval"] is True
    assert tools["coding.exec"]["mutates_state"] is False
    assert tools["coding.exec"]["network"] == "none"

    captured: dict[str, object] = {}

    def fake_build(repo_root, *, image, command, cfg, network_allowed, **options):
        captured.update(
            {
                "repo_root": repo_root,
                "image": image,
                "command": list(command),
                "network_allowed": network_allowed,
                "options": dict(options),
            }
        )
        return object()

    def fake_run(plan, *, cfg):
        del plan, cfg
        return {
            "status": "CODING_CONTAINER_EXECUTED_PASS",
            "execution_status": "PASS",
            "plan_sha256": "b" * 64,
            "head_sha": git(repo, "rev-parse", "HEAD").strip(),
            "command_argv": command,
            "exit_code": 0,
            "duration_ms": 12,
            "stdout": {"byte_size": 3, "sha256": "c" * 64, "text": "ok\n"},
            "stderr": {"byte_size": 0, "sha256": "d" * 64, "text": ""},
            "source_mount_read_only": True,
            "workspace_ephemeral": True,
            "network_allowed": False,
            "receipt_sha256": "e" * 64,
        }

    monkeypatch.setattr(tools_module, "build_coding_container_plan", fake_build)
    monkeypatch.setattr(tools_module, "run_coding_container_plan", fake_run)

    requested = request_tool_call(
        "coding.exec",
        {"repo_root": str(repo), "image": image, "command": command, "timeout_seconds": 45},
        cfg=cfg,
        requested_by="test",
        reason="isolated command execution regression",
    )
    tool_call_id = requested["tool_call"]["tool_call_id"]

    with pytest.raises(ToolRuntimeError) as blocked:
        run_tool_call(tool_call_id, cfg=cfg, executed_by="test")
    assert blocked.value.status == "ERROR_TOOL_APPROVAL_REQUIRED"

    approve_tool_call(tool_call_id, cfg=cfg, approved_by="test")
    executed = run_tool_call(tool_call_id, cfg=cfg, executed_by="test")

    assert executed["result"]["status"] == "CODING_EXEC_OK"
    assert executed["result"]["execution_status"] == "PASS"
    assert executed["result"]["container_receipt_sha256"] == "e" * 64
    assert executed["result"]["source_mount_read_only"] is True
    assert executed["result"]["workspace_ephemeral"] is True
    assert executed["result"]["network_allowed"] is False
    assert captured == {
        "repo_root": str(repo),
        "image": image,
        "command": command,
        "network_allowed": False,
        "options": {"timeout_seconds": 45},
    }


def test_coding_exec_rejects_network_enablement(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    requested = request_tool_call(
        "coding.exec",
        {
            "repo_root": str(repo),
            "image": "sha256:" + ("a" * 64),
            "command": ["python", "-V"],
            "network_allowed": True,
        },
        cfg=cfg,
        requested_by="test",
    )
    tool_call_id = requested["tool_call"]["tool_call_id"]
    approve_tool_call(tool_call_id, cfg=cfg, approved_by="test")

    with pytest.raises(ToolRuntimeError) as blocked:
        run_tool_call(tool_call_id, cfg=cfg, executed_by="test")
    assert blocked.value.status == "ERROR_CODING_EXEC_NETWORK_POLICY"

