from __future__ import annotations

from pathlib import Path

from backend.app.coding_checks import CodingCheck, run_coding_check_suite
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


def test_check_suite_closes_accounting_and_fails_required_checks(tmp_path: Path, monkeypatch) -> None:
    cfg = make_settings(tmp_path)
    calls = []

    class Plan:
        def __init__(self, command):
            self.command = tuple(command)

    def fake_plan(repo_root, *, image, command, cfg, mount_mode, network_allowed):
        calls.append((tuple(command), mount_mode, network_allowed))
        return Plan(command)

    def fake_execute(plan):
        passed = "fail" not in plan.command
        return {
            "status": "CODING_CONTAINER_PASS" if passed else "CODING_CONTAINER_FAIL",
            "returncode": 0 if passed else 1,
            "stdout": "ok" if passed else "",
            "stderr": "" if passed else "failed",
            "stdout_sha256": "a" * 64,
            "stderr_sha256": "b" * 64,
            "stdout_bytes": 2 if passed else 0,
            "stderr_bytes": 0 if passed else 6,
            "plan_sha256": "c" * 64,
            "image": PINNED,
            "network_allowed": False,
            "mount_mode": "read_only",
        }

    monkeypatch.setattr("backend.app.coding_checks.build_coding_container_plan", fake_plan)
    monkeypatch.setattr("backend.app.coding_checks.execute_coding_container", fake_execute)

    report = run_coding_check_suite(
        tmp_path,
        image=PINNED,
        cfg=cfg,
        checks=[
            CodingCheck("pytest", "test", ("pytest", "-q"), True),
            CodingCheck("lint", "lint", ("ruff", "check", "."), True),
            CodingCheck("optional", "benchmark", ("fail",), False),
        ],
    )

    assert report["status"] == "CODING_CHECK_SUITE_PASS"
    assert report["check_count"] == 3
    assert report["pass_count"] == 2
    assert report["fail_count"] == 1
    assert report["required_failure_count"] == 0
    assert all(report["invariants"].values())
    assert all(mount == "read_only" and network is False for _command, mount, network in calls)


def test_required_failure_fails_suite(tmp_path: Path, monkeypatch) -> None:
    cfg = make_settings(tmp_path)

    class Plan:
        command = ("pytest",)

    monkeypatch.setattr("backend.app.coding_checks.build_coding_container_plan", lambda *args, **kwargs: Plan())
    monkeypatch.setattr(
        "backend.app.coding_checks.execute_coding_container",
        lambda plan: {
            "status": "CODING_CONTAINER_FAIL",
            "returncode": 1,
            "stdout": "",
            "stderr": "failed",
            "stdout_sha256": "a" * 64,
            "stderr_sha256": "b" * 64,
            "stdout_bytes": 0,
            "stderr_bytes": 6,
            "plan_sha256": "c" * 64,
            "image": PINNED,
            "network_allowed": False,
            "mount_mode": "read_only",
        },
    )

    report = run_coding_check_suite(tmp_path, image=PINNED, cfg=cfg, checks=[CodingCheck("pytest", "test", ("pytest",), True)])
    assert report["status"] == "CODING_CHECK_SUITE_FAIL"
    assert report["required_failures"] == ["pytest"]
