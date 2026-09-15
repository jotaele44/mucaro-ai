from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.app.coding_patch import validate_patch_proposal
from backend.app.coding_workspace import CodingWorkspaceError
from backend.app.config import Settings

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required for coding patch tests")


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


def patch_for(old: str = "alpha", new: str = "beta") -> str:
    return (
        "diff --git a/tracked.txt b/tracked.txt\n"
        "--- a/tracked.txt\n"
        "+++ b/tracked.txt\n"
        "@@ -1 +1 @@\n"
        f"-{old}\n"
        f"+{new}\n"
    )


def test_patch_check_records_paths_hashes_and_no_mutation(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    patch = patch_for()
    before = (repo / "tracked.txt").read_bytes()

    receipt = validate_patch_proposal(repo, patch, cfg=cfg)

    assert receipt["status"] == "CODING_PATCH_CHECK_OK"
    assert receipt["applicable"] is True
    assert receipt["mutation_performed"] is False
    assert receipt["patch_paths"] == ["tracked.txt"]
    assert receipt["patch_path_count"] == 1
    assert receipt["dirty_overlap"] == []
    assert receipt["patch_sha256"] == hashlib.sha256(patch.encode("utf-8")).hexdigest()
    assert receipt["preimages"] == [
        {
            "path": "tracked.txt",
            "state": "EXISTS",
            "byte_size": len(before),
            "sha256": hashlib.sha256(before).hexdigest(),
        }
    ]
    assert (repo / "tracked.txt").read_bytes() == before


def test_patch_check_preserves_dirty_overlap_as_warning(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    (repo / "tracked.txt").write_text("local\n", encoding="utf-8")
    patch = patch_for(old="local", new="beta")

    receipt = validate_patch_proposal(repo, patch, cfg=cfg)

    assert receipt["status"] == "CODING_PATCH_CHECK_WARN"
    assert receipt["applicable"] is True
    assert receipt["dirty_overlap"] == ["tracked.txt"]
    assert "CODING_PATCH_DIRTY_OVERLAP" in receipt["warnings"]
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "local\n"


def test_patch_check_returns_rejected_receipt_when_git_apply_check_fails(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    patch = patch_for(old="does-not-match", new="beta")

    receipt = validate_patch_proposal(repo, patch, cfg=cfg)

    assert receipt["status"] == "CODING_PATCH_CHECK_REJECTED"
    assert receipt["applicable"] is False
    assert "CODING_PATCH_NOT_APPLICABLE" in receipt["warnings"]
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "alpha\n"


def test_patch_check_rejects_git_admin_traversal_format_and_size(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)

    git_admin = "diff --git a/.git/config b/.git/config\n--- a/.git/config\n+++ b/.git/config\n@@ -1 +1 @@\n-a\n+b\n"
    with pytest.raises(CodingWorkspaceError) as blocked_admin:
        validate_patch_proposal(repo, git_admin, cfg=cfg)
    assert blocked_admin.value.status == "ERROR_CODING_GIT_ADMIN_PATH_BLOCKED"

    traversal = "diff --git a/../outside.txt b/../outside.txt\n--- a/../outside.txt\n+++ b/../outside.txt\n@@ -1 +1 @@\n-a\n+b\n"
    with pytest.raises(CodingWorkspaceError) as blocked_traversal:
        validate_patch_proposal(repo, traversal, cfg=cfg)
    assert blocked_traversal.value.status == "ERROR_CODING_PATCH_PATH_OUTSIDE_REPOSITORY"

    with pytest.raises(CodingWorkspaceError) as bad_format:
        validate_patch_proposal(repo, "--- a/tracked.txt\n+++ b/tracked.txt\n", cfg=cfg)
    assert bad_format.value.status == "ERROR_CODING_PATCH_FORMAT"

    with pytest.raises(CodingWorkspaceError) as oversized:
        validate_patch_proposal(repo, patch_for(), cfg=cfg, max_bytes=5)
    assert oversized.value.status == "ERROR_CODING_PATCH_TOO_LARGE"
