from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from backend.app.coding_patch import validate_patch_proposal
from backend.app.config import Settings

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required for quoted patch path tests")


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


def test_quoted_rename_paths_are_decoded_into_exact_candidate_set(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "mucaro-tests@example.invalid")
    git(repo, "config", "user.name", "Mucaro Tests")
    (repo / "old name.txt").write_text("alpha\n", encoding="utf-8")
    git(repo, "add", "old name.txt")
    git(repo, "commit", "-m", "initial")

    git(repo, "mv", "old name.txt", "new name.txt")
    patch = git(repo, "diff", "--cached", "--binary", "--find-renames")
    assert "rename from" in patch
    assert "rename to" in patch
    git(repo, "reset", "--hard", "HEAD")

    receipt = validate_patch_proposal(repo, patch, cfg=cfg)

    assert receipt["status"] == "CODING_PATCH_CHECK_OK"
    assert receipt["applicable"] is True
    assert receipt["patch_paths"] == ["new name.txt", "old name.txt"]
    assert all('"' not in path for path in receipt["patch_paths"])
    preimage = {item["path"]: item for item in receipt["preimages"]}
    assert preimage["old name.txt"]["state"] == "EXISTS"
    assert preimage["new name.txt"]["state"] == "MISSING"
