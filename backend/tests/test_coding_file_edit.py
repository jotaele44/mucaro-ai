from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

import backend.app.coding_mutation as mutation_module
import backend.app.tools as tools_module
from backend.app.coding_mutation import CodingMutationError, apply_file_edit_transaction
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
    (repo / "nested").mkdir()
    (repo / "nested" / "keep.txt").write_text("keep\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "initial")
    return repo


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def preimage(repo: Path, path: str) -> dict[str, object]:
    target = repo / path
    if not target.exists():
        return {"path": path, "state": "MISSING", "byte_size": None, "sha256": None}
    raw = target.read_bytes()
    return {
        "path": path,
        "state": "EXISTS",
        "byte_size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_replace_preserves_unrelated_dirty_work_and_head(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    expected_head = head(repo)
    expected = preimage(repo, "tracked.txt")
    (repo / "unrelated.txt").write_text("operator work\n", encoding="utf-8")

    receipt = apply_file_edit_transaction(
        repo,
        "tracked.txt",
        "REPLACE",
        expected_head_sha=expected_head,
        expected_preimage=expected,
        content="beta\n",
        expected_content_sha256=content_hash("beta\n"),
        cfg=cfg,
    )

    assert receipt["status"] == "CODING_FILE_EDIT_APPLIED"
    assert receipt["operation"] == "REPLACE"
    assert receipt["path"] == "tracked.txt"
    assert receipt["head_sha"] == expected_head
    assert head(repo) == expected_head
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "beta\n"
    assert (repo / "unrelated.txt").read_text(encoding="utf-8") == "operator work\n"
    assert receipt["unrelated_dirty_paths_before"] == ["unrelated.txt"]
    assert receipt["unrelated_dirty_paths_after"] == ["unrelated.txt"]
    assert receipt["postimage"]["sha256"] == content_hash("beta\n")
    assert all(receipt["invariants"].values())


def test_create_and_delete_are_explicit_state_transitions(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)

    create_root = tmp_path / "create"
    create_root.mkdir()
    create_repo = make_repo(create_root)
    create_receipt = apply_file_edit_transaction(
        create_repo,
        "nested/new.py",
        "CREATE",
        expected_head_sha=head(create_repo),
        expected_preimage=preimage(create_repo, "nested/new.py"),
        content="print('created')\n",
        expected_content_sha256=content_hash("print('created')\n"),
        cfg=cfg,
    )
    assert create_receipt["status"] == "CODING_FILE_EDIT_APPLIED"
    assert (create_repo / "nested" / "new.py").read_text(encoding="utf-8") == "print('created')\n"

    delete_root = tmp_path / "delete"
    delete_root.mkdir()
    delete_repo = make_repo(delete_root)
    delete_receipt = apply_file_edit_transaction(
        delete_repo,
        "tracked.txt",
        "DELETE",
        expected_head_sha=head(delete_repo),
        expected_preimage=preimage(delete_repo, "tracked.txt"),
        cfg=cfg,
    )
    assert delete_receipt["status"] == "CODING_FILE_EDIT_APPLIED"
    assert not (delete_repo / "tracked.txt").exists()
    assert delete_receipt["postimage"] == {
        "path": "tracked.txt",
        "state": "MISSING",
        "byte_size": None,
        "sha256": None,
    }


@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("../escape.txt", "ERROR_CODING_FILE_EDIT_PATH_INVALID"),
        ("./tracked.txt", "ERROR_CODING_FILE_EDIT_PATH_NONCANONICAL"),
        (".git/config", "ERROR_CODING_FILE_EDIT_GIT_INTERNAL"),
        ("nested//new.py", "ERROR_CODING_FILE_EDIT_PATH_NONCANONICAL"),
    ],
)
def test_file_edit_rejects_noncanonical_or_git_internal_paths(tmp_path: Path, path: str, status: str) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    with pytest.raises(CodingMutationError) as blocked:
        apply_file_edit_transaction(
            repo,
            path,
            "CREATE",
            expected_head_sha=head(repo),
            expected_preimage={"path": path, "state": "MISSING", "byte_size": None, "sha256": None},
            content="x\n",
            expected_content_sha256=content_hash("x\n"),
            cfg=cfg,
        )
    assert blocked.value.status == status


def test_file_edit_rejects_head_preimage_content_dirty_and_symlink_drift(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    expected = preimage(repo, "tracked.txt")
    expected_head = head(repo)

    with pytest.raises(CodingMutationError) as bad_head:
        apply_file_edit_transaction(
            repo,
            "tracked.txt",
            "REPLACE",
            expected_head_sha="0" * 40,
            expected_preimage=expected,
            content="beta\n",
            expected_content_sha256=content_hash("beta\n"),
            cfg=cfg,
        )
    assert bad_head.value.status == "ERROR_CODING_FILE_EDIT_HEAD_DRIFT"

    with pytest.raises(CodingMutationError) as bad_content:
        apply_file_edit_transaction(
            repo,
            "tracked.txt",
            "REPLACE",
            expected_head_sha=expected_head,
            expected_preimage=expected,
            content="beta\n",
            expected_content_sha256="f" * 64,
            cfg=cfg,
        )
    assert bad_content.value.status == "ERROR_CODING_FILE_EDIT_CONTENT_DRIFT"

    (repo / "tracked.txt").write_text("operator change\n", encoding="utf-8")
    with pytest.raises(CodingMutationError) as dirty:
        apply_file_edit_transaction(
            repo,
            "tracked.txt",
            "REPLACE",
            expected_head_sha=expected_head,
            expected_preimage=expected,
            content="beta\n",
            expected_content_sha256=content_hash("beta\n"),
            cfg=cfg,
        )
    assert dirty.value.status == "ERROR_CODING_FILE_EDIT_DIRTY_OVERLAP"

    git(repo, "checkout", "--", "tracked.txt")
    bad_preimage = dict(expected)
    bad_preimage["sha256"] = "e" * 64
    with pytest.raises(CodingMutationError) as preimage_drift:
        apply_file_edit_transaction(
            repo,
            "tracked.txt",
            "REPLACE",
            expected_head_sha=expected_head,
            expected_preimage=bad_preimage,
            content="beta\n",
            expected_content_sha256=content_hash("beta\n"),
            cfg=cfg,
        )
    assert preimage_drift.value.status == "ERROR_CODING_FILE_EDIT_PREIMAGE_DRIFT"

    symlink = repo / "nested" / "link.txt"
    try:
        symlink.symlink_to(repo / "tracked.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this platform")
    with pytest.raises(CodingMutationError) as symlink_block:
        apply_file_edit_transaction(
            repo,
            "nested/link.txt",
            "REPLACE",
            expected_head_sha=expected_head,
            expected_preimage={
                "path": "nested/link.txt",
                "state": "EXISTS",
                "byte_size": len(b"alpha\n"),
                "sha256": hashlib.sha256(b"alpha\n").hexdigest(),
            },
            content="beta\n",
            expected_content_sha256=content_hash("beta\n"),
            cfg=cfg,
        )
    assert symlink_block.value.status == "ERROR_CODING_FILE_EDIT_SYMLINK"


def test_failed_postcondition_rolls_back_exact_preimage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    expected_head = head(repo)
    expected = preimage(repo, "tracked.txt")
    real_inspect = mutation_module.inspect_repository
    calls = {"count": 0}

    def drift_once(repo_root, *, cfg):
        calls["count"] += 1
        receipt = real_inspect(repo_root, cfg=cfg)
        if calls["count"] == 2:
            receipt = dict(receipt)
            receipt["head_sha"] = "f" * 40
        return receipt

    monkeypatch.setattr(mutation_module, "inspect_repository", drift_once)

    with pytest.raises(CodingMutationError) as rolled_back:
        apply_file_edit_transaction(
            repo,
            "tracked.txt",
            "REPLACE",
            expected_head_sha=expected_head,
            expected_preimage=expected,
            content="beta\n",
            expected_content_sha256=content_hash("beta\n"),
            cfg=cfg,
        )

    assert rolled_back.value.status == "CODING_FILE_EDIT_ROLLED_BACK"
    assert rolled_back.value.receipt is not None
    assert rolled_back.value.receipt["rollback_succeeded"] is True
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "alpha\n"
    assert head(repo) == expected_head
    assert git(repo, "status", "--porcelain").strip() == ""


def test_governed_file_edit_requires_approval_and_binds_exact_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = make_settings(tmp_path)
    captured: dict[str, object] = {}

    def fake_edit(repo_root, path, operation, **kwargs):
        captured.update({"repo_root": repo_root, "path": path, "operation": operation, **kwargs})
        return {
            "status": "CODING_FILE_EDIT_APPLIED",
            "operation": operation,
            "path": path,
            "postimage": {"path": path, "state": "EXISTS", "byte_size": 2, "sha256": kwargs["expected_content_sha256"]},
        }

    monkeypatch.setattr(tools_module, "apply_file_edit_transaction", fake_edit)
    args = {
        "repo_root": str(tmp_path / "repo"),
        "path": "a.py",
        "operation": "CREATE",
        "expected_head_sha": "1" * 40,
        "expected_preimage": {"path": "a.py", "state": "MISSING", "byte_size": None, "sha256": None},
        "content": "x\n",
        "expected_content_sha256": content_hash("x\n"),
    }
    requested = request_tool_call("coding.file_edit", args, cfg=cfg, requested_by="test")
    call_id = requested["tool_call"]["tool_call_id"]

    with pytest.raises(ToolRuntimeError) as blocked:
        run_tool_call(call_id, cfg=cfg, executed_by="test")
    assert blocked.value.status == "ERROR_TOOL_APPROVAL_REQUIRED"

    approve_tool_call(call_id, cfg=cfg, approved_by="test")
    result = run_tool_call(call_id, cfg=cfg, executed_by="test")["result"]
    assert result["status"] == "CODING_FILE_EDIT_APPLIED"
    assert captured["repo_root"] == args["repo_root"]
    assert captured["path"] == "a.py"
    assert captured["operation"] == "CREATE"
    assert captured["expected_head_sha"] == "1" * 40
    assert captured["expected_preimage"] == args["expected_preimage"]
    assert captured["content"] == "x\n"
    assert captured["expected_content_sha256"] == content_hash("x\n")
    assert tools_module.TOOL_REGISTRY["coding.file_edit"].mutates_state is True
    assert tools_module.TOOL_REGISTRY["coding.file_edit"].network == "none"
