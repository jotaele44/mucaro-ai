"""`code checks` exit status: a failed or blocked suite must never look like success to a script."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.app import cli_code, coding_container
from backend.app.cli import main
from backend.tests.test_coding_agent_hardening import granted_repo
from backend.tests.test_coding_checks import PINNED

CHECK = ["--check", "test:unit=pytest -q"]


def base(repo: Path, *extra: str) -> list[str]:
    return ["code", "checks", "--repo", str(repo), "--image", PINNED, *CHECK, *extra]


def fake_suite(execution_status: Any) -> Any:
    def suite(repo: Any, *, image: str, checks: list[Any], cfg: Any, on_event: Any = None, engine: Any = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"status": f"CODING_CHECK_SUITE_EXECUTED_{execution_status}"}
        if execution_status is not None:
            payload["execution_status"] = execution_status
        return payload

    return suite


@pytest.mark.parametrize(
    ("execution_status", "expected"),
    [("PASS", 0), ("FAIL", 1), ("BLOCKED", 3), ("INVALIDATED", 1), ("SOMETHING_NEW", 1), (None, 1)],
    ids=["pass", "fail", "blocked", "invalidated", "unknown", "absent"],
)
@pytest.mark.parametrize("stream", [False, True], ids=["plain", "stream"])
def test_a_run_exits_zero_only_when_the_suite_passed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, execution_status: Any, expected: int, stream: bool
) -> None:
    cfg, repo = granted_repo(tmp_path)
    monkeypatch.setattr(cli_code, "execute_coding_check_suite", fake_suite(execution_status))

    code = main(base(repo, "--run", "--yes", *(["--stream"] if stream else [])), cfg)

    captured = capsys.readouterr()
    assert code == expected
    # The result is reported either way (pretty-printed when plain, the last NDJSON line when streaming): the exit status is the signal.
    result = json.loads(captured.out.splitlines()[-1] if stream else captured.out)
    assert result["status"] == f"CODING_CHECK_SUITE_EXECUTED_{execution_status}" and captured.err == ""


def test_a_plan_is_blocked_when_no_engine_can_be_found(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, repo = granted_repo(tmp_path)
    monkeypatch.setattr(coding_container, "resolve_container_engine", lambda preferred=None: preferred or "/usr/bin/docker")
    assert main(base(repo), cfg) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "CODING_CHECK_SUITE_PLAN_READY"

    def no_engine(preferred: Any = None) -> str:
        raise coding_container.ContainerRuntimeError("ERROR_CONTAINER_ENGINE_UNAVAILABLE", "docker,podman")

    monkeypatch.setattr(coding_container, "resolve_container_engine", no_engine)
    assert main(base(repo), cfg) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "CODING_CHECK_SUITE_PLAN_BLOCKED"


def test_errors_keep_their_own_exit_status(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, repo = granted_repo(tmp_path)
    monkeypatch.setattr(cli_code, "execute_coding_check_suite", fake_suite("PASS"))

    assert main(base(repo, "--run"), cfg) == 2  # no approval: an error, reported on stderr, nothing run
    assert json.loads(capsys.readouterr().err)["status"] == "ERROR_CODING_CONFIRMATION_REQUIRED"
    assert main(["code", "checks", "--repo", str(repo)], cfg) == 2  # no --image/--check/--profile
    assert json.loads(capsys.readouterr().err)["status"] == "ERROR_CODING_CHECK_SPEC"
