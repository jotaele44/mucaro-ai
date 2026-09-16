from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.app.coding_checks as coding_checks
import backend.app.coding_container as coding_container
from backend.app.coding_checks import CodingCheck, execute_coding_check_suite
from backend.app.coding_container import CodingContainerError, CodingContainerPlan, execute_coding_container_plan

PINNED = "ghcr.io/jotaele44/mucaro-coding@sha256:" + "a" * 64


def make_plan(root: Path, *, max_output_bytes: int = 1024) -> CodingContainerPlan:
    return CodingContainerPlan(
        repo_root=root,
        image=PINNED,
        command=("pytest", "-q"),
        head_sha="1" * 40,
        dirty_status="CLEAN",
        dirty_paths_sha256=hashlib.sha256(b"[]").hexdigest(),
        timeout_seconds=30,
        max_output_bytes=max_output_bytes,
        memory_mb=512,
        cpus=1.0,
        pids_limit=32,
        engine="/usr/bin/docker",
    )


def valid_validation(plan: CodingContainerPlan) -> dict[str, object]:
    return {
        "status": "CODING_CONTAINER_PLAN_VALID",
        "plan_sha256": plan.plan_sha256,
        "head_match": True,
        "dirty_state_match": True,
    }


def test_live_execution_receipt_requires_zero_exit_and_binds_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = make_plan(tmp_path)
    monkeypatch.setattr(coding_container, "validate_coding_container_plan", lambda current, cfg=None: valid_validation(current))
    monkeypatch.setattr(coding_container, "build_coding_container_command", lambda current: ["fake-container", *current.command])

    def fake_run(argv, **kwargs):
        kwargs["stdout"].write(b"tests passed\n")
        kwargs["stderr"].write(b"")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(coding_container.subprocess, "run", fake_run)
    receipt = execute_coding_container_plan(plan)

    assert receipt["executed"] is True
    assert receipt["passed"] is True
    assert receipt["exit_code"] == 0
    assert receipt["status"] == "CODING_CONTAINER_EXECUTION_PASS"
    assert receipt["network_allowed"] is False
    assert receipt["repository_read_only"] is True
    assert receipt["rootfs_read_only"] is True
    assert receipt["stdout_sha256"] == hashlib.sha256(b"tests passed\n").hexdigest()
    assert receipt["receipt_sha256"]


def test_nonzero_exit_is_executed_fail_not_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = make_plan(tmp_path)
    monkeypatch.setattr(coding_container, "validate_coding_container_plan", lambda current, cfg=None: valid_validation(current))
    monkeypatch.setattr(coding_container, "build_coding_container_command", lambda current: ["fake-container", *current.command])

    def fake_run(argv, **kwargs):
        kwargs["stdout"].write(b"")
        kwargs["stderr"].write(b"failure\n")
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(coding_container.subprocess, "run", fake_run)
    receipt = execute_coding_container_plan(plan)

    assert receipt["executed"] is True
    assert receipt["passed"] is False
    assert receipt["exit_code"] == 7
    assert receipt["status"] == "CODING_CONTAINER_EXECUTION_FAIL"


def test_stale_preflight_blocks_before_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = make_plan(tmp_path)
    monkeypatch.setattr(
        coding_container,
        "validate_coding_container_plan",
        lambda current, cfg=None: {"status": "CODING_CONTAINER_PLAN_STALE"},
    )

    def forbidden_run(*args, **kwargs):
        raise AssertionError("subprocess must not launch for stale plan")

    monkeypatch.setattr(coding_container.subprocess, "run", forbidden_run)
    with pytest.raises(CodingContainerError) as exc:
        execute_coding_container_plan(plan)
    assert exc.value.status == "ERROR_CODING_CONTAINER_PLAN_STALE"


def test_oversized_output_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = make_plan(tmp_path, max_output_bytes=4)
    monkeypatch.setattr(coding_container, "validate_coding_container_plan", lambda current, cfg=None: valid_validation(current))
    monkeypatch.setattr(coding_container, "build_coding_container_command", lambda current: ["fake-container", *current.command])

    def fake_run(argv, **kwargs):
        kwargs["stdout"].write(b"12345")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(coding_container.subprocess, "run", fake_run)
    with pytest.raises(CodingContainerError) as exc:
        execute_coding_container_plan(plan)
    assert exc.value.status == "ERROR_CODING_CONTAINER_OUTPUT_TOO_LARGE"


def test_suite_pass_requires_every_required_live_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_plan(repo_root, *, image, command, cfg=None, network_allowed=False):
        plan = make_plan(tmp_path)
        return CodingContainerPlan(**{**plan.__dict__, "command": tuple(command)})

    def fake_execute(plan, cfg=None):
        passed = plan.command[0] != "fail"
        return {
            "executed": True,
            "passed": passed,
            "exit_code": 0 if passed else 1,
            "network_allowed": False,
            "repository_read_only": True,
            "receipt_sha256": "f" * 64,
        }

    monkeypatch.setattr(coding_checks, "build_coding_container_plan", fake_plan)
    monkeypatch.setattr(coding_checks, "execute_coding_container_plan", fake_execute)

    report = execute_coding_check_suite(
        tmp_path,
        image=PINNED,
        checks=[
            CodingCheck("tests", "test", ("pass",), True),
            CodingCheck("lint", "lint", ("fail",), True),
            CodingCheck("benchmark", "benchmark", ("pass",), False),
        ],
    )

    assert report["status"] == "CODING_CHECK_SUITE_NOT_PASS"
    assert report["execution_status"] == "EXECUTED"
    assert report["state_counts"] == {"PASS": 2, "FAIL": 1, "BLOCKED": 0}
    assert report["required_failures"] == ["lint"]
    assert report["invariants"]["all_required_pass"] is False


def test_suite_blocked_row_cannot_be_promoted_to_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(coding_checks, "build_coding_container_plan", lambda *args, **kwargs: make_plan(tmp_path))

    def blocked(plan, cfg=None):
        raise CodingContainerError("ERROR_CODING_CONTAINER_START", "engine unavailable")

    monkeypatch.setattr(coding_checks, "execute_coding_container_plan", blocked)
    report = execute_coding_check_suite(
        tmp_path,
        image=PINNED,
        checks=[CodingCheck("tests", "test", ("pytest",), True)],
    )

    assert report["state_counts"] == {"PASS": 0, "FAIL": 0, "BLOCKED": 1}
    assert report["required_failures"] == ["tests"]
    assert report["status"] == "CODING_CHECK_SUITE_NOT_PASS"
