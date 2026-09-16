from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import backend.app.coding_checks as coding_checks
import backend.app.coding_container as coding_container
from backend.app.coding_checks import CodingCheck, CodingCheckError, build_coding_check_suite_plan, execute_coding_check_suite
from backend.app.coding_container import CodingContainerError
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
    assert all(row["mount_mode"] == "read_only_source_ephemeral_workspace" for row in report["checks"])


def test_execution_suite_requires_terminal_receipts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    monkeypatch.setattr(coding_container, "resolve_container_engine", lambda preferred=None: "/usr/bin/docker")

    outcomes = iter(["PASS", "FAIL", "PASS"])

    def fake_run(plan, *, cfg):
        outcome = next(outcomes)
        return {
            "status": f"CODING_CONTAINER_EXECUTED_{outcome}",
            "execution_status": outcome,
            "plan_sha256": plan.plan_sha256,
            "receipt_sha256": "b" * 64,
        }

    monkeypatch.setattr(coding_checks, "run_coding_container_plan", fake_run)
    report = execute_coding_check_suite(
        repo,
        image=PINNED,
        cfg=cfg,
        checks=[
            CodingCheck("pytest", "test", ("pytest", "-q"), True),
            CodingCheck("lint", "lint", ("ruff", "check", "."), True),
            CodingCheck("benchmark", "benchmark", ("python", "bench.py"), False),
        ],
    )

    assert report["status"] == "CODING_CHECK_SUITE_EXECUTED_FAIL"
    assert report["execution_status"] == "FAIL"
    assert report["state_counts"] == {"PASS": 2, "FAIL": 1, "BLOCKED": 0}
    assert report["required_failures"] == ["lint"]
    assert report["required_blocked"] == []
    assert report["invariants"]["row_accounting_closed"] is True
    assert report["invariants"]["required_pass_closure"] is False


def test_execution_suite_blocked_outweighs_command_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    monkeypatch.setattr(coding_container, "resolve_container_engine", lambda preferred=None: "/usr/bin/docker")
    calls = 0

    def fake_run(plan, *, cfg):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise CodingContainerError("ERROR_CODING_CONTAINER_START", "engine unavailable")
        return {
            "status": "CODING_CONTAINER_EXECUTED_FAIL",
            "execution_status": "FAIL",
            "plan_sha256": plan.plan_sha256,
            "receipt_sha256": "c" * 64,
        }

    monkeypatch.setattr(coding_checks, "run_coding_container_plan", fake_run)
    report = execute_coding_check_suite(
        repo,
        image=PINNED,
        cfg=cfg,
        checks=[
            CodingCheck("pytest", "test", ("pytest",), True),
            CodingCheck("lint", "lint", ("ruff", "check", "."), True),
        ],
    )

    assert report["status"] == "CODING_CHECK_SUITE_EXECUTED_BLOCKED"
    assert report["execution_status"] == "BLOCKED"
    assert report["state_counts"] == {"PASS": 0, "FAIL": 1, "BLOCKED": 1}
    assert report["required_blocked"] == ["pytest"]
    assert report["required_failures"] == ["lint"]


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
