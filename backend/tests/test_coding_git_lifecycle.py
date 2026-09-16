from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.app.coding_git_lifecycle import (
    CodingGitLifecycleError,
    build_git_lifecycle_plan,
    validate_git_lifecycle_plan,
)
from backend.app.config import Settings


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
    git(repo, "branch", "-M", "main")
    (repo / "tracked.txt").write_text("alpha\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "initial")
    return repo


def test_lifecycle_plan_binds_head_staged_paths_and_staged_bytes(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    (repo / "tracked.txt").write_text("beta\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    head = git(repo, "rev-parse", "HEAD").strip()

    plan = build_git_lifecycle_plan(
        repo,
        target_branch="feature/safe-change",
        commit_message="coding: safe change",
        expected_head_sha=head,
        pr_base_branch="main",
        cfg=cfg,
    )
    receipt = validate_git_lifecycle_plan(plan, cfg=cfg)

    assert receipt["status"] == "CODING_GIT_LIFECYCLE_PLAN_VALID"
    assert receipt["head_match"] is True
    assert receipt["staged_paths_match"] is True
    assert receipt["staged_bytes_match"] is True
    assert receipt["target_branch_absent"] is True
    assert receipt["force_allowed"] is False
    assert receipt["push_allowed"] is False
    assert receipt["remote_mutation_allowed"] is False
    assert receipt["pr_plan"]["remote_action"] == "NOT_EXECUTED"
    assert "--no-verify" not in receipt["commit_argv"]
    assert len(receipt["staged_diff_sha256"]) == 64


def test_same_staged_path_with_changed_bytes_invalidates_plan(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    (repo / "tracked.txt").write_text("beta\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    head = git(repo, "rev-parse", "HEAD").strip()
    plan = build_git_lifecycle_plan(
        repo,
        target_branch="feature/byte-drift",
        commit_message="coding: bind bytes",
        expected_head_sha=head,
        cfg=cfg,
    )

    (repo / "tracked.txt").write_text("gamma\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    receipt = validate_git_lifecycle_plan(plan, cfg=cfg)

    assert receipt["staged_paths_match"] is True
    assert receipt["staged_bytes_match"] is False
    assert receipt["status"] == "CODING_GIT_LIFECYCLE_PLAN_STALE"


def test_protected_or_existing_target_branch_fails_closed(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    (repo / "tracked.txt").write_text("beta\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    head = git(repo, "rev-parse", "HEAD").strip()

    with pytest.raises(CodingGitLifecycleError) as protected:
        build_git_lifecycle_plan(repo, target_branch="main", commit_message="x", expected_head_sha=head, cfg=cfg)
    assert protected.value.status == "ERROR_CODING_GIT_PROTECTED_BRANCH"

    git(repo, "branch", "feature/existing")
    with pytest.raises(CodingGitLifecycleError) as existing:
        build_git_lifecycle_plan(
            repo,
            target_branch="feature/existing",
            commit_message="x",
            expected_head_sha=head,
            cfg=cfg,
        )
    assert existing.value.status == "ERROR_CODING_GIT_BRANCH_EXISTS"


def test_head_drift_and_empty_stage_fail_closed(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD").strip()

    with pytest.raises(CodingGitLifecycleError) as empty:
        build_git_lifecycle_plan(
            repo,
            target_branch="feature/nothing",
            commit_message="x",
            expected_head_sha=head,
            cfg=cfg,
        )
    assert empty.value.status == "ERROR_CODING_GIT_NOTHING_STAGED"

    (repo / "tracked.txt").write_text("beta\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    with pytest.raises(CodingGitLifecycleError) as drift:
        build_git_lifecycle_plan(
            repo,
            target_branch="feature/drift",
            commit_message="x",
            expected_head_sha="0" * 40,
            cfg=cfg,
        )
    assert drift.value.status == "ERROR_CODING_GIT_HEAD_DRIFT"
