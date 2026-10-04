"""Engine preflight: an unavailable engine or image is BLOCKED infrastructure, never a failing check (S3).

docs/CODING_CAPABILITY_STATUS.md: "A nonzero command is FAIL; engine/image/isolation failure is BLOCKED."
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from backend.app import coding_checks, coding_container, container_runtime
from backend.app.cli import main
from backend.app.coding_checks import CodingCheck, build_coding_check_suite_plan, execute_coding_check_suite
from backend.app.coding_container import CodingContainerError, build_coding_container_plan, run_coding_container_plan
from backend.tests.test_coding_agent_hardening import granted_repo
from backend.tests.test_coding_container import PINNED, make_repo, make_settings

ENGINE = "/usr/bin/docker"
ID_IMAGE = "sha256:" + "d" * 64
REAL_RUN = subprocess.run
DAEMON_DOWN = b"Cannot connect to the Docker daemon at unix:///x.sock. Is the docker daemon running?"


class FakeEngine:
    """Stands in for a Docker or Podman binary: answers `info`, `image inspect` and `run` as configured."""

    def __init__(
        self,
        *,
        info_rc: int = 0,
        info_stderr: bytes = b"",
        inspect_rc: int = 0,
        inspect_stdout: bytes = b"sha256:" + b"1" * 64 + b"\n",
        inspect_stderr: bytes = b"",
        run_rc: int = 0,
        run_stdout: bytes = b"2 passed\n",
        run_stderr: bytes = b"",
    ) -> None:
        self.info = (info_rc, b"", info_stderr)
        self.inspect = (inspect_rc, inspect_stdout, inspect_stderr)
        self.run = (run_rc, run_stdout, run_stderr)
        self.calls: list[list[str]] = []

    def __call__(self, argv: Any, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if not argv or argv[0] != ENGINE:
            return REAL_RUN(argv, *args, **kwargs)  # git and everything else is real
        self.calls.append(list(argv))
        answers = {"info": self.info, "image": self.inspect, "run": self.run}
        if argv[1] not in answers:
            raise AssertionError(f"unexpected engine call: {argv}")
        returncode, stdout, stderr = answers[argv[1]]
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)


def prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine: FakeEngine, *, image: str = PINNED) -> tuple[Any, Any]:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    monkeypatch.setattr(coding_container, "resolve_container_engine", lambda preferred=None: preferred or ENGINE)
    plan = build_coding_container_plan(repo, image=image, command=["pytest", "-q"], cfg=cfg)
    monkeypatch.setattr(subprocess, "run", engine)
    return cfg, plan


def test_stopped_daemon_is_blocked_infrastructure_not_a_failing_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = FakeEngine(info_rc=1, info_stderr=DAEMON_DOWN)
    cfg, plan = prepared(tmp_path, monkeypatch, engine)

    with pytest.raises(CodingContainerError) as info:
        run_coding_container_plan(plan, cfg=cfg)

    assert info.value.status == "ERROR_CODING_CONTAINER_ENGINE_UNAVAILABLE" and "daemon" in info.value.detail
    assert engine.calls == [[ENGINE, "info"]]  # the contained command never ran


def test_missing_image_is_blocked_before_anything_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = FakeEngine(inspect_rc=1, inspect_stderr=b"Error: No such image: " + PINNED.encode())
    cfg, plan = prepared(tmp_path, monkeypatch, engine)

    with pytest.raises(CodingContainerError) as info:
        run_coding_container_plan(plan, cfg=cfg)

    assert info.value.status == "ERROR_CODING_CONTAINER_IMAGE_MISSING" and PINNED in info.value.detail
    assert engine.calls == [[ENGINE, "info"], [ENGINE, "image", "inspect", "--format", "{{.Id}}", PINNED]]


@pytest.mark.parametrize(
    "reported",
    [ID_IMAGE.encode() + b"\n", ID_IMAGE.removeprefix("sha256:").encode() + b"\n"],
    ids=["docker-with-prefix", "podman-without-prefix"],
)
def test_local_image_id_is_verified_for_docker_and_podman_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reported: bytes) -> None:
    engine = FakeEngine(inspect_stdout=reported)
    cfg, plan = prepared(tmp_path, monkeypatch, engine, image=ID_IMAGE)

    receipt = run_coding_container_plan(plan, cfg=cfg)

    assert receipt["execution_status"] == "PASS" and receipt["observed_image_id"] == ID_IMAGE


def test_local_image_id_mismatch_is_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = FakeEngine(inspect_stdout=b"sha256:" + b"e" * 64 + b"\n")
    cfg, plan = prepared(tmp_path, monkeypatch, engine, image=ID_IMAGE)

    with pytest.raises(CodingContainerError) as info:
        run_coding_container_plan(plan, cfg=cfg)

    assert info.value.status == "ERROR_CODING_CONTAINER_IMAGE_IDENTITY_MISMATCH"
    assert [call[1] for call in engine.calls] == ["info", "image"]


def test_registry_digest_images_are_presence_checked_and_the_local_id_is_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A registry digest is the manifest hash, not the local image ID, so the two must not be compared.
    engine = FakeEngine(inspect_stdout=b"sha256:" + b"9" * 64 + b"\n")
    cfg, plan = prepared(tmp_path, monkeypatch, engine)

    receipt = run_coding_container_plan(plan, cfg=cfg)

    assert receipt["image"] == PINNED and receipt["observed_image_id"] == "sha256:" + "9" * 64


def test_a_failing_command_after_a_healthy_preflight_is_still_a_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = FakeEngine(run_rc=7, run_stderr=b"1 failed\n")
    cfg, plan = prepared(tmp_path, monkeypatch, engine)

    receipt = run_coding_container_plan(plan, cfg=cfg)

    assert receipt["status"] == "CODING_CONTAINER_EXECUTED_FAIL" and receipt["exit_code"] == 7
    assert [call[1] for call in engine.calls] == ["info", "image", "run"]


def test_engine_error_exit_is_blocked_but_a_command_exiting_125_is_a_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = FakeEngine(run_rc=125, run_stderr=b"docker: Error response from daemon: OCI runtime create failed\n")
    cfg, plan = prepared(tmp_path, monkeypatch, engine)
    with pytest.raises(CodingContainerError) as info:
        run_coding_container_plan(plan, cfg=cfg)
    assert info.value.status == "ERROR_CODING_CONTAINER_ENGINE_ERROR"

    quirky = FakeEngine(run_rc=125, run_stderr=b"the test suite exited oddly\n")
    monkeypatch.setattr(subprocess, "run", quirky)
    receipt = run_coding_container_plan(plan, cfg=cfg)
    assert receipt["status"] == "CODING_CONTAINER_EXECUTED_FAIL" and receipt["exit_code"] == 125


def test_suite_counts_an_unavailable_engine_as_blocked_not_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    monkeypatch.setattr(coding_container, "resolve_container_engine", lambda preferred=None: preferred or ENGINE)
    monkeypatch.setattr(subprocess, "run", FakeEngine(info_rc=1, info_stderr=DAEMON_DOWN))

    report = execute_coding_check_suite(repo, image=PINNED, cfg=cfg, checks=[CodingCheck("unit", "test", ("pytest", "-q"), True)])

    assert report["status"] == "CODING_CHECK_SUITE_EXECUTED_BLOCKED" and report["state_counts"] == {"PASS": 0, "FAIL": 0, "BLOCKED": 1}
    assert report["checks"][0]["error_status"] == "ERROR_CODING_CONTAINER_ENGINE_UNAVAILABLE"
    assert report["required_blocked"] == ["unit"] and report["required_failures"] == []


def test_engine_path_reaches_the_container_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []

    def resolver(preferred: str | None = None) -> str:
        seen.append(preferred)
        return preferred or ENGINE

    monkeypatch.setattr(coding_container, "resolve_container_engine", resolver)
    monkeypatch.setattr(coding_checks, "run_coding_container_plan", lambda plan, *, cfg: {"execution_status": "PASS"})
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    checks = [CodingCheck("unit", "test", ("pytest",), True)]

    build_coding_check_suite_plan(repo, image=PINNED, cfg=cfg, engine="/opt/docker/bin/docker", checks=checks)
    execute_coding_check_suite(repo, image=PINNED, cfg=cfg, engine="/opt/docker/bin/docker", checks=checks)
    build_coding_check_suite_plan(repo, image=PINNED, cfg=cfg, checks=checks)  # unchanged default

    assert seen == ["/opt/docker/bin/docker", "/opt/docker/bin/docker", None]


def test_cli_engine_option_makes_checks_plannable_without_docker_on_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, repo = granted_repo(tmp_path)
    fake_docker = tmp_path / "bin" / "docker"
    fake_docker.parent.mkdir()
    fake_docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_docker.chmod(0o755)
    real_which = shutil.which
    monkeypatch.setattr(
        container_runtime.shutil, "which", lambda command, *a, **k: None if command in {"docker", "podman"} else real_which(command, *a, **k)
    )
    argv = ["code", "checks", "--repo", str(repo), "--image", PINNED, "--check", "test:unit=pytest -q"]

    assert main(argv, cfg) == 3  # a plan that infrastructure blocks is not a success
    row = json.loads(capsys.readouterr().out)["checks"][0]
    assert row["state"] == "BLOCKED" and row["error_status"] == "ERROR_CONTAINER_ENGINE_UNAVAILABLE"

    assert main([*argv, "--engine", str(fake_docker)], cfg) == 0
    assert json.loads(capsys.readouterr().out)["checks"][0]["state"] == "PLANNED"
