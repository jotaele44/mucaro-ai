from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.app.coding_workspace import (
    CodingWorkspaceError,
    inspect_repository,
    inspect_repository_diff,
    read_repository_file,
)
from backend.app.config import Settings
from backend.app.permissions import grant_directory

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required for coding workspace tests")


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
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def make_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir(parents=True)
    git(repo, "init")
    git(repo, "config", "user.email", "mucaro-tests@example.invalid")
    git(repo, "config", "user.name", "Mucaro Tests")
    (repo / "tracked.txt").write_text("alpha\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "initial")
    return repo


def test_repository_status_closes_clean_and_dirty_path_accounting(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)

    clean = inspect_repository(repo, cfg=cfg)
    assert clean["status"] == "CODING_REPOSITORY_STATUS_OK"
    assert clean["dirty_status"] == "CLEAN"
    assert clean["dirty_path_count"] == 0
    assert clean["tracked_file_count"] == 1
    assert clean["head_sha"] == git(repo, "rev-parse", "HEAD").strip()

    (repo / "tracked.txt").write_text("beta\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("new\n", encoding="utf-8")
    dirty = inspect_repository(repo, cfg=cfg)

    assert dirty["dirty_status"] == "DIRTY"
    assert dirty["unstaged_files"] == ["tracked.txt"]
    assert dirty["untracked_files"] == ["untracked.txt"]
    assert dirty["dirty_paths"] == ["tracked.txt", "untracked.txt"]
    assert dirty["dirty_path_count"] == len(dirty["dirty_paths"])


def test_repository_file_read_preserves_exact_utf8_and_byte_hash(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)

    receipt = read_repository_file(repo, "tracked.txt", cfg=cfg)
    raw = b"alpha\n"

    assert receipt["status"] == "CODING_FILE_READ_OK"
    assert receipt["tracked"] is True
    assert receipt["content"] == raw.decode("utf-8")
    assert receipt["byte_size"] == len(raw)
    assert receipt["sha256"] == hashlib.sha256(raw).hexdigest()
    assert receipt["encoding_state"] == "UTF8"
    assert receipt["line_count"] == 1


def test_repository_file_read_does_not_synthesize_binary_text(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    (repo / "binary.bin").write_bytes(b"\xff\x00\xfe")

    receipt = read_repository_file(repo, "binary.bin", cfg=cfg)

    assert receipt["tracked"] is False
    assert receipt["content"] is None
    assert receipt["encoding_state"] == "BINARY_OR_NON_UTF8"
    assert receipt["line_count"] is None


def test_repository_file_read_rejects_path_escape_and_size_overflow(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    (tmp_path / "outside.txt").write_text("outside", encoding="utf-8")

    with pytest.raises(CodingWorkspaceError) as escape:
        read_repository_file(repo, "../outside.txt", cfg=cfg)
    assert escape.value.status == "ERROR_CODING_FILE_OUTSIDE_REPOSITORY"

    with pytest.raises(CodingWorkspaceError) as oversized:
        read_repository_file(repo, "tracked.txt", cfg=cfg, max_bytes=2)
    assert oversized.value.status == "ERROR_CODING_FILE_TOO_LARGE"


def test_repository_diff_reports_tracked_diff_and_untracked_residue_separately(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    (repo / "tracked.txt").write_text("beta\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("new\n", encoding="utf-8")

    receipt = inspect_repository_diff(repo, cfg=cfg)

    assert receipt["status"] == "CODING_DIFF_OK"
    assert receipt["dirty_status"] == "DIRTY"
    assert receipt["changed_files"] == ["tracked.txt"]
    assert receipt["untracked_files"] == ["untracked.txt"]
    assert receipt["changed_file_count"] == 1
    assert receipt["untracked_file_count"] == 1
    assert "-alpha" in receipt["diff"]
    assert "+beta" in receipt["diff"]
    assert receipt["diff_sha256"] == hashlib.sha256(receipt["diff"].encode("utf-8")).hexdigest()


def test_repository_diff_rejects_invalid_base_ref_and_output_overflow(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    (repo / "tracked.txt").write_text("beta\n", encoding="utf-8")

    with pytest.raises(CodingWorkspaceError) as invalid:
        inspect_repository_diff(repo, cfg=cfg, base="HEAD..other")
    assert invalid.value.status == "ERROR_CODING_BASE_REF_INVALID"

    with pytest.raises(CodingWorkspaceError) as oversized:
        inspect_repository_diff(repo, cfg=cfg, max_bytes=1)
    assert oversized.value.status == "ERROR_CODING_DIFF_TOO_LARGE"


def test_external_repository_requires_explicit_directory_grant(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    app_root.mkdir()
    cfg = make_settings(app_root)
    external_root = tmp_path / "external"
    external_root.mkdir()
    repo = make_repo(external_root)

    with pytest.raises(CodingWorkspaceError) as blocked:
        inspect_repository(repo, cfg=cfg)
    assert blocked.value.status == "ERROR_DIRECTORY_PERMISSION_REQUIRED"

    grant = grant_directory(repo, cfg, granted_by="test", reason="coding workspace regression")
    assert grant["status"] == "DIRECTORY_PERMISSION_GRANTED"

    receipt = inspect_repository(repo, cfg=cfg)
    assert receipt["status"] == "CODING_REPOSITORY_STATUS_OK"
    assert receipt["permission_scope"] == "granted_directory"
