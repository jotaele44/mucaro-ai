from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import backend.app.coding_container as coding_container
from backend.app.coding_checks import CodingCheck, CodingCheckError, build_coding_check_suite_plan
from backend.app.config import Settings

PINNED = "ghcr.io/jotaele44/mucaro-coding@sha256:" + "a" * 64


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


def git(repo: Path, *args: str) -> None:
    proc = subprocess.run(["git", *args], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    assert proc.returncode == 0, proc.stderr


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


def test_check_suite_plan_closes_accounting_without_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    monkeypatch.setattr(coding_container, "resolve_container_engine", lambda preferred=None: preferred or "/usr/bin/docker")

    report = build_coding_check_suite_plan(
        repo,
        image=PINNED,
        cfg=cfg,
        checks=[
            CodingCheck("pytest", "test", ("pytest", "-q"), True),
            CodingCheck("lint", "lint", ("ruff", "check", "."), True),
            CodingCheck("types", "type", ("mypy", "backend"), True),
            CodingCheck("build", "build", ("python", "-m", "build"), True),
        ],
    )

    assert report["status"] == "CODING_CHECK_SUITE_PLAN_READY"
    assert report["execution_status"] == "NOT_EXECUTED"
    assert report["check_count"] == 4
    assert report["state_counts"] == {"PLANNED": 4, "STALE": 0, "BLOCKED": 0}
    assert report["required_planning_failure_count"] == 0
    assert all(report["invariants"].values())
    assert all(row["network_allowed"] is False for row in report["checks"])
    assert all(row["mount_mode"] == "read_only" for row in report["checks"])


def test_duplicate_check_id_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    monkeypatch.setattr(coding_container, "resolve_container_engine", lambda preferred=None: "/usr/bin/docker")

    with pytest.raises(CodingCheckError) as exc:
        build_coding_check_suite_plan(
            repo,
            image=PINNED,
            cfg=cfg,
            checks=[
                CodingCheck("same", "test", ("pytest",), True),
                CodingCheck("same", "lint", ("ruff", "check", "."), True),
            ],
        )
    assert exc.value.status == "ERROR_CODING_CHECK_DUPLICATE"


def test_invalid_kind_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    monkeypatch.setattr(coding_container, "resolve_container_engine", lambda preferred=None: "/usr/bin/docker")

    with pytest.raises(CodingCheckError) as exc:
        build_coding_check_suite_plan(
            repo,
            image=PINNED,
            cfg=cfg,
            checks=[CodingCheck("bad", "arbitrary", ("echo", "bad"), True)],
        )
    assert exc.value.status == "ERROR_CODING_CHECK_DEFINITION"
