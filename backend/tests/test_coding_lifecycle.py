from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from backend.app.coding_lifecycle import (
    CodingLifecycleError,
    build_pull_request_request,
    commit_approved_changes,
    create_work_branch_transaction,
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
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "mucaro-tests@example.invalid")
    git(repo, "config", "user.name", "Mucaro Tests")
    (repo / "tracked.txt").write_text("alpha\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "initial")
    return repo


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def test_work_branch_requires_exact_clean_head_and_preserves_sha(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    base = head(repo)

    receipt = create_work_branch_transaction(repo, branch_name="feature/demo", expected_head_sha=base, cfg=cfg)

    assert receipt["status"] == "CODING_BRANCH_CREATED"
    assert receipt["original_branch"] == "main"
    assert receipt["branch"] == "feature/demo"
    assert receipt["head_sha"] == base
    assert head(repo) == base
    assert git(repo, "branch", "--show-current").strip() == "feature/demo"
    assert all(receipt["invariants"].values())


def test_work_branch_rejects_protected_dirty_existing_and_stale_cases(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    base = head(repo)

    with pytest.raises(CodingLifecycleError) as protected:
        create_work_branch_transaction(repo, branch_name="main", expected_head_sha=base, cfg=cfg)
    assert protected.value.status == "ERROR_CODING_PROTECTED_BRANCH"

    (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(CodingLifecycleError) as dirty:
        create_work_branch_transaction(repo, branch_name="feature/dirty", expected_head_sha=base, cfg=cfg)
    assert dirty.value.status == "ERROR_CODING_BRANCH_DIRTY_REPOSITORY"
    git(repo, "reset", "--hard", "HEAD")

    with pytest.raises(CodingLifecycleError) as stale:
        create_work_branch_transaction(repo, branch_name="feature/stale", expected_head_sha="0" * 40, cfg=cfg)
    assert stale.value.status == "ERROR_CODING_BRANCH_HEAD_DRIFT"

    git(repo, "branch", "feature/existing")
    with pytest.raises(CodingLifecycleError) as exists:
        create_work_branch_transaction(repo, branch_name="feature/existing", expected_head_sha=base, cfg=cfg)
    assert exists.value.status == "ERROR_CODING_BRANCH_EXISTS"


def test_commit_requires_exact_nonprotected_branch_and_dirty_candidate_set(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    base = head(repo)
    create_work_branch_transaction(repo, branch_name="feature/demo", expected_head_sha=base, cfg=cfg)
    (repo / "tracked.txt").write_text("beta\n", encoding="utf-8")
    (repo / "new.txt").write_text("new\n", encoding="utf-8")

    receipt = commit_approved_changes(
        repo,
        expected_head_sha=base,
        expected_branch="feature/demo",
        approved_paths=["tracked.txt", "new.txt"],
        message="Apply approved coding change",
        cfg=cfg,
    )

    assert receipt["status"] == "CODING_COMMIT_CREATED"
    assert receipt["parent_sha"] == base
    assert receipt["commit_sha"] == head(repo)
    assert receipt["commit_sha"] != base
    assert receipt["approved_paths"] == ["new.txt", "tracked.txt"]
    assert receipt["commit_changed_paths"] == ["new.txt", "tracked.txt"]
    assert receipt["dirty_status_after"] == "CLEAN"
    assert all(receipt["invariants"].values())


def test_commit_rejects_dirty_set_drift_and_prestaged_state(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    base = head(repo)
    create_work_branch_transaction(repo, branch_name="feature/demo", expected_head_sha=base, cfg=cfg)
    (repo / "tracked.txt").write_text("beta\n", encoding="utf-8")
    (repo / "extra.txt").write_text("extra\n", encoding="utf-8")

    with pytest.raises(CodingLifecycleError) as drift:
        commit_approved_changes(
            repo,
            expected_head_sha=base,
            expected_branch="feature/demo",
            approved_paths=["tracked.txt"],
            message="Only one file",
            cfg=cfg,
        )
    assert drift.value.status == "ERROR_CODING_COMMIT_DIRTY_SET_DRIFT"

    git(repo, "add", "tracked.txt", "extra.txt")
    with pytest.raises(CodingLifecycleError) as staged:
        commit_approved_changes(
            repo,
            expected_head_sha=base,
            expected_branch="feature/demo",
            approved_paths=["tracked.txt", "extra.txt"],
            message="Prestaged",
            cfg=cfg,
        )
    assert staged.value.status == "ERROR_CODING_COMMIT_PRESTAGED_STATE"


def test_failed_commit_rolls_index_back_and_preserves_worktree(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    base = head(repo)
    create_work_branch_transaction(repo, branch_name="feature/demo", expected_head_sha=base, cfg=cfg)
    (repo / "tracked.txt").write_text("beta\n", encoding="utf-8")

    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 9\n", encoding="utf-8")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)

    with pytest.raises(CodingLifecycleError) as exc:
        commit_approved_changes(
            repo,
            expected_head_sha=base,
            expected_branch="feature/demo",
            approved_paths=["tracked.txt"],
            message="Blocked by hook",
            cfg=cfg,
        )

    assert exc.value.status == "CODING_COMMIT_ROLLED_BACK"
    assert exc.value.receipt is not None
    assert exc.value.receipt["head_sha"] == base
    assert exc.value.receipt["dirty_paths"] == ["tracked.txt"]
    assert git(repo, "diff", "--cached", "--name-only").strip() == ""
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "beta\n"


def test_pr_request_binds_clean_branch_head_and_payload_without_network_action(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    base = head(repo)
    create_work_branch_transaction(repo, branch_name="feature/demo", expected_head_sha=base, cfg=cfg)
    (repo / "tracked.txt").write_text("beta\n", encoding="utf-8")
    commit = commit_approved_changes(
        repo,
        expected_head_sha=base,
        expected_branch="feature/demo",
        approved_paths=["tracked.txt"],
        message="Change tracked file",
        cfg=cfg,
    )

    request = build_pull_request_request(
        repo,
        expected_head_sha=commit["commit_sha"],
        head_branch="feature/demo",
        base_branch="main",
        title="Governed change",
        body="Evidence-bound pull request request.",
        cfg=cfg,
    )

    assert request["status"] == "CODING_PR_REQUEST_READY"
    assert request["head_sha"] == commit["commit_sha"]
    assert request["head_branch"] == "feature/demo"
    assert request["base_branch"] == "main"
    assert request["network_action_performed"] is False
    assert request["requires_authenticated_remote_adapter"] is True
    assert len(request["request_sha256"]) == 64
    assert all(request["invariants"].values())


def test_pr_request_rejects_dirty_or_branch_mismatch(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    base = head(repo)
    create_work_branch_transaction(repo, branch_name="feature/demo", expected_head_sha=base, cfg=cfg)

    with pytest.raises(CodingLifecycleError) as mismatch:
        build_pull_request_request(
            repo,
            expected_head_sha=base,
            head_branch="feature/other",
            base_branch="main",
            title="Mismatch",
            body="",
            cfg=cfg,
        )
    assert mismatch.value.status == "ERROR_CODING_PR_BRANCH_DRIFT"

    (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(CodingLifecycleError) as dirty:
        build_pull_request_request(
            repo,
            expected_head_sha=base,
            head_branch="feature/demo",
            base_branch="main",
            title="Dirty",
            body="",
            cfg=cfg,
        )
    assert dirty.value.status == "ERROR_CODING_PR_DIRTY_REPOSITORY"
