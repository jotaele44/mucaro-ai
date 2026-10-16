from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

import backend.app.tools as tools_module
from backend.app.coding_file_edit import CodingFileEditError, edit_repository_file_transaction
from backend.app.config import Settings
from backend.app.tools import ToolRuntimeError, approve_tool_call, request_tool_call, run_tool_call

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required for coding file-edit tests")


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
    (repo / "unrelated.txt").write_text("stable\n", encoding="utf-8")
    git(repo, "add", "tracked.txt", "unrelated.txt")
    git(repo, "commit", "-m", "initial")
    return repo


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_create_preserves_unrelated_dirty_work_and_exact_content(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    (repo / "unrelated.txt").write_text("local\n", encoding="utf-8")
    head = git(repo, "rev-parse", "HEAD").strip()
    content = "created\n"

    receipt = edit_repository_file_transaction(
        repo,
        "nested/new.txt",
        operation="CREATE",
        expected_head_sha=head,
        expected_preimage_state="MISSING",
        content=content,
        expected_content_sha256=sha256_text(content),
        cfg=cfg,
    )

    assert receipt["status"] == "CODING_FILE_EDIT_APPLIED"
    assert receipt["operation"] == "CREATE"
    assert receipt["path"] == "nested/new.txt"
    assert receipt["postimage"]["sha256"] == sha256_text(content)
    assert (repo / "nested" / "new.txt").read_text(encoding="utf-8") == content
    assert (repo / "unrelated.txt").read_text(encoding="utf-8") == "local\n"
    assert git(repo, "rev-parse", "HEAD").strip() == head
    assert receipt["unexpected_new_dirty_paths"] == []
    assert all(receipt["invariants"].values())


def test_replace_and_delete_require_exact_clean_preimages(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD").strip()
    original = (repo / "tracked.txt").read_bytes()
    original_hash = hashlib.sha256(original).hexdigest()
    replacement = "beta\n"

    replaced = edit_repository_file_transaction(
        repo,
        "tracked.txt",
        operation="REPLACE",
        expected_head_sha=head,
        expected_preimage_state="EXISTS",
        expected_preimage_sha256=original_hash,
        content=replacement,
        expected_content_sha256=sha256_text(replacement),
        cfg=cfg,
    )
    assert replaced["postimage"]["sha256"] == sha256_text(replacement)

    with pytest.raises(CodingFileEditError) as dirty:
        edit_repository_file_transaction(
            repo,
            "tracked.txt",
            operation="DELETE",
            expected_head_sha=head,
            expected_preimage_state="EXISTS",
            expected_preimage_sha256=sha256_text(replacement),
            cfg=cfg,
        )
    assert dirty.value.status == "ERROR_CODING_FILE_EDIT_DIRTY_TARGET"


def test_head_preimage_content_and_path_drift_fail_closed(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD").strip()
    current_hash = hashlib.sha256((repo / "tracked.txt").read_bytes()).hexdigest()

    with pytest.raises(CodingFileEditError) as bad_head:
        edit_repository_file_transaction(
            repo,
            "new.txt",
            operation="CREATE",
            expected_head_sha="0" * 40,
            expected_preimage_state="MISSING",
            content="x",
            expected_content_sha256=sha256_text("x"),
            cfg=cfg,
        )
    assert bad_head.value.status == "ERROR_CODING_FILE_EDIT_HEAD_DRIFT"

    with pytest.raises(CodingFileEditError) as bad_preimage:
        edit_repository_file_transaction(
            repo,
            "tracked.txt",
            operation="REPLACE",
            expected_head_sha=head,
            expected_preimage_state="EXISTS",
            expected_preimage_sha256="f" * 64,
            content="x",
            expected_content_sha256=sha256_text("x"),
            cfg=cfg,
        )
    assert bad_preimage.value.status == "ERROR_CODING_FILE_EDIT_PREIMAGE_DRIFT"

    with pytest.raises(CodingFileEditError) as bad_content:
        edit_repository_file_transaction(
            repo,
            "new.txt",
            operation="CREATE",
            expected_head_sha=head,
            expected_preimage_state="MISSING",
            content="x",
            expected_content_sha256="e" * 64,
            cfg=cfg,
        )
    assert bad_content.value.status == "ERROR_CODING_FILE_EDIT_CONTENT_DRIFT"

    for path in ("../escape.txt", "/absolute.txt", ".git/config"):
        with pytest.raises(CodingFileEditError):
            edit_repository_file_transaction(
                repo,
                path,
                operation="CREATE",
                expected_head_sha=head,
                expected_preimage_state="MISSING",
                content="x",
                expected_content_sha256=sha256_text("x"),
                cfg=cfg,
            )

    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "alpha\n"
    assert current_hash == hashlib.sha256((repo / "tracked.txt").read_bytes()).hexdigest()


def test_symlink_component_is_rejected(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (repo / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    head = git(repo, "rev-parse", "HEAD").strip()

    with pytest.raises(CodingFileEditError) as exc:
        edit_repository_file_transaction(
            repo,
            "linked/escape.txt",
            operation="CREATE",
            expected_head_sha=head,
            expected_preimage_state="MISSING",
            content="x",
            expected_content_sha256=sha256_text("x"),
            cfg=cfg,
        )
    assert exc.value.status in {"ERROR_CODING_FILE_EDIT_PATH_BOUNDARY", "ERROR_CODING_FILE_EDIT_SYMLINK_FORBIDDEN"}
    assert not (outside / "escape.txt").exists()


def test_coding_file_edit_crosses_governed_approval_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = make_settings(tmp_path)
    captured: dict[str, object] = {}

    def fake_edit(repo_root, path, **kwargs):
        captured.update({"repo_root": repo_root, "path": path, **{k: v for k, v in kwargs.items() if k != "cfg"}})
        return {
            "status": "CODING_FILE_EDIT_APPLIED",
            "operation": kwargs["operation"],
            "path": path,
            "postimage": {"state": "EXISTS", "sha256": kwargs["expected_content_sha256"]},
        }

    monkeypatch.setattr(tools_module, "edit_repository_file_transaction", fake_edit)
    content = "bounded\n"
    arguments = {
        "repo_root": str(tmp_path / "repo"),
        "path": "src/example.py",
        "operation": "CREATE",
        "expected_head_sha": "1" * 40,
        "expected_preimage_state": "MISSING",
        "content": content,
        "expected_content_sha256": sha256_text(content),
    }
    requested = request_tool_call("coding.file_edit", arguments, cfg=cfg, requested_by="test")
    call_id = requested["tool_call"]["tool_call_id"]

    with pytest.raises(ToolRuntimeError) as blocked:
        run_tool_call(call_id, cfg=cfg, executed_by="test")
    assert blocked.value.status == "ERROR_TOOL_APPROVAL_REQUIRED"

    approve_tool_call(call_id, cfg=cfg, approved_by="test")
    result = run_tool_call(call_id, cfg=cfg, executed_by="test")["result"]

    assert result["status"] == "CODING_FILE_EDIT_APPLIED"
    assert captured["path"] == "src/example.py"
    assert captured["expected_head_sha"] == "1" * 40
    assert captured["expected_content_sha256"] == sha256_text(content)
