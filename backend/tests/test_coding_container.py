from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

import backend.app.coding_container as coding_container
from backend.app.coding_container import (
    CodingContainerError,
    build_coding_container_command,
    build_coding_container_plan,
    run_coding_container_plan,
    validate_coding_container_plan,
)
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
    (repo / "tracked.txt").write_text("alpha\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "initial")
    return repo


def fake_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(coding_container, "resolve_container_engine", lambda preferred=None: preferred or "/usr/bin/docker")


def test_plan_uses_read_only_source_and_ephemeral_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    fake_engine(monkeypatch)

    plan = build_coding_container_plan(repo, image=PINNED, command=["python", "-m", "pytest", "-q"], cfg=cfg)
    argv = build_coding_container_command(plan)

    assert plan.head_sha == git(repo, "rev-parse", "HEAD").strip()
    assert plan.dirty_status == "CLEAN"
    assert plan.mount_mode == "read_only_source_ephemeral_workspace"
    assert plan.network_allowed is False
    assert "--read-only" in argv
    assert "--pull=never" in argv
    assert "--cap-drop=ALL" in argv
    assert "--security-opt=no-new-privileges" in argv
    assert "--network=none" in argv
    assert "--ipc=none" in argv
    assert "--user=65532:65532" in argv
    assert f"--mount=type=bind,src={repo.resolve()},dst=/workspace/source,readonly" in argv
    assert any(token.startswith("--tmpfs=/workspace/repo:rw,") for token in argv)
    assert not any(f"src={repo.resolve()},dst=/workspace/source" in token and "readonly" not in token for token in argv)

    validation = validate_coding_container_plan(plan, cfg=cfg)
    assert validation["status"] == "CODING_CONTAINER_PLAN_VALID"
    assert validation["head_match"] is True
    assert validation["dirty_state_match"] is True
    assert validation["source_mount_read_only"] is True
    assert validation["workspace_ephemeral"] is True


def test_network_enablement_and_unpinned_images_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    fake_engine(monkeypatch)

    with pytest.raises(CodingContainerError) as network:
        build_coding_container_plan(repo, image=PINNED, command=["pytest"], cfg=cfg, network_allowed=True)
    assert network.value.status == "ERROR_CODING_CONTAINER_NETWORK_POLICY"

    with pytest.raises(CodingContainerError) as image:
        build_coding_container_plan(repo, image="ghcr.io/jotaele44/mucaro-coding:latest", command=["pytest"], cfg=cfg)
    assert image.value.status == "ERROR_CONTAINER_IMAGE_NOT_PINNED"


def test_coding_container_is_disabled_for_app_store_distribution(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    cfg = Settings(**{**cfg.__dict__, "distribution_channel": "mac_app_store"})

    with pytest.raises(CodingContainerError) as exc:
        build_coding_container_plan(
            tmp_path / "repo-does-not-need-to-exist",
            image=PINNED,
            command=["pytest"],
            cfg=cfg,
        )

    assert exc.value.status == "ERROR_DISTRIBUTION_RUNTIME_POLICY"


def test_dirty_candidate_drift_makes_plan_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    fake_engine(monkeypatch)
    plan = build_coding_container_plan(repo, image=PINNED, command=["ruff", "check", "."], cfg=cfg)

    (repo / "tracked.txt").write_text("beta\n", encoding="utf-8")
    validation = validate_coding_container_plan(plan, cfg=cfg)

    assert validation["status"] == "CODING_CONTAINER_PLAN_STALE"
    assert validation["head_match"] is True
    assert validation["dirty_state_match"] is False


def test_head_drift_makes_plan_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    fake_engine(monkeypatch)
    plan = build_coding_container_plan(repo, image=PINNED, command=["mypy", "backend"], cfg=cfg)

    (repo / "second.txt").write_text("second\n", encoding="utf-8")
    git(repo, "add", "second.txt")
    git(repo, "commit", "-m", "second")
    validation = validate_coding_container_plan(plan, cfg=cfg)

    assert validation["status"] == "CODING_CONTAINER_PLAN_STALE"
    assert validation["head_match"] is False


def test_execution_receipt_closes_pass_and_preserves_host_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    fake_engine(monkeypatch)
    plan = build_coding_container_plan(repo, image=PINNED, command=["pytest", "-q"], cfg=cfg)

    def fake_container(argv, **kwargs):
        assert argv[0] == "/usr/bin/docker"
        assert argv[2] == f"--name={kwargs['container_name']}"
        return 0, b"2 passed\n", b""

    monkeypatch.setattr(coding_container, "_run_container_bounded", fake_container)
    receipt = run_coding_container_plan(plan, cfg=cfg)

    assert receipt["status"] == "CODING_CONTAINER_EXECUTED_PASS"
    assert receipt["execution_status"] == "PASS"
    assert receipt["exit_code"] == 0
    assert receipt["stdout"]["text"] == "2 passed\n"
    assert len(receipt["stdout"]["sha256"]) == 64
    assert receipt["source_mount_read_only"] is True
    assert receipt["workspace_ephemeral"] is True
    assert receipt["network_allowed"] is False
    assert receipt["implicit_image_pull_allowed"] is False
    assert receipt["invariants"]["head_conserved"] is True
    assert receipt["invariants"]["dirty_state_conserved"] is True
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "alpha\n"


def test_nonzero_command_is_a_fail_receipt_not_an_infrastructure_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    fake_engine(monkeypatch)
    plan = build_coding_container_plan(repo, image=PINNED, command=["pytest", "-q"], cfg=cfg)

    def fake_container(argv, **kwargs):
        assert argv[0] == "/usr/bin/docker"
        assert argv[2] == f"--name={kwargs['container_name']}"
        return 7, b"", b"failing test\n"

    monkeypatch.setattr(coding_container, "_run_container_bounded", fake_container)
    receipt = run_coding_container_plan(plan, cfg=cfg)

    assert receipt["status"] == "CODING_CONTAINER_EXECUTED_FAIL"
    assert receipt["execution_status"] == "FAIL"
    assert receipt["exit_code"] == 7
    assert receipt["stderr"]["text"] == "failing test\n"


def test_host_state_drift_during_execution_invalidates_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    fake_engine(monkeypatch)
    plan = build_coding_container_plan(repo, image=PINNED, command=["pytest"], cfg=cfg)
    mutated = False

    def fake_container(argv, **kwargs):
        nonlocal mutated
        assert argv[0] == "/usr/bin/docker"
        assert argv[2] == f"--name={kwargs['container_name']}"
        if not mutated:
            (repo / "tracked.txt").write_text("tampered\n", encoding="utf-8")
            mutated = True
        return 0, b"ok\n", b""

    monkeypatch.setattr(coding_container, "_run_container_bounded", fake_container)
    with pytest.raises(CodingContainerError) as exc:
        run_coding_container_plan(plan, cfg=cfg)

    assert exc.value.status == "ERROR_CODING_CONTAINER_SOURCE_DRIFT"
    assert exc.value.receipt is not None
    assert exc.value.receipt["status"] == "CODING_CONTAINER_SOURCE_DRIFT"
    assert exc.value.receipt["execution_status"] == "INVALIDATED"


def test_command_resource_mount_and_output_limits_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    fake_engine(monkeypatch)

    with pytest.raises(CodingContainerError) as empty:
        build_coding_container_plan(repo, image=PINNED, command=[], cfg=cfg)
    assert empty.value.status == "ERROR_CODING_CONTAINER_COMMAND"

    with pytest.raises(CodingContainerError) as nul:
        build_coding_container_plan(repo, image=PINNED, command=["python\x00bad"], cfg=cfg)
    assert nul.value.status == "ERROR_CODING_CONTAINER_COMMAND"

    with pytest.raises(CodingContainerError) as limit:
        build_coding_container_plan(repo, image=PINNED, command=["pytest"], cfg=cfg, workspace_mb=0)
    assert limit.value.status == "ERROR_CODING_CONTAINER_LIMIT"

    plan = build_coding_container_plan(repo, image=PINNED, command=["pytest"], cfg=cfg, max_output_bytes=4)

    def fake_container(argv, **kwargs):
        assert argv[0] == "/usr/bin/docker"
        assert argv[2] == f"--name={kwargs['container_name']}"
        return 0, b"12345", b""

    monkeypatch.setattr(coding_container, "_run_container_bounded", fake_container)
    with pytest.raises(CodingContainerError) as output:
        run_coding_container_plan(plan, cfg=cfg)
    assert output.value.status == "ERROR_CODING_CONTAINER_OUTPUT_TOO_LARGE"


def test_exact_local_image_id_is_immutable_coding_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    fake_engine(monkeypatch)
    local_id = "sha256:" + "b" * 64

    plan = build_coding_container_plan(repo, image=local_id, command=["python", "--version"], cfg=cfg)
    argv = build_coding_container_command(plan)

    assert plan.image == local_id
    assert local_id in argv


FAKE_ENGINE_SOURCE = """#!{python}
import os, sys, time
log = os.environ["MUCARO_FAKE_ENGINE_LOG"]
if sys.argv[1] == "rm":
    with open(log, "a") as handle:
        handle.write("rm " + " ".join(sys.argv[2:]) + "\\n")
    sys.exit(0)
with open(log, "a") as handle:
    handle.write("run " + next(arg for arg in sys.argv if arg.startswith("--name=")) + "\\n")
mode = os.environ["MUCARO_FAKE_ENGINE_MODE"]
if mode == "flood_stdout":
    chunk = b"x" * 65536
    while True:
        os.write(1, chunk)
elif mode == "flood_stderr":
    chunk = b"e" * 65536
    while True:
        os.write(2, chunk)
elif mode == "hang":
    os.write(1, b"started\\n")
    time.sleep(600)
elif mode == "ok":
    os.write(1, b"o" * 1000)
    os.write(2, b"warn\\n")
    sys.exit(3)
"""


def real_fake_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> tuple[str, Path]:
    """Route the engine argv to a real child process without needing an executable file.

    The coding profile runs this suite inside the coding container, where /tmp is mounted noexec, so the fake engine
    is a script interpreted by ``sys.executable`` rather than a file that is exec'd directly. The real ``Popen`` and
    ``run`` still execute, so the bounded reader, the kill path and the container removal are exercised end to end.
    """

    engine = str(tmp_path / "fake-engine")
    script = tmp_path / "fake_engine.py"
    script.write_text(FAKE_ENGINE_SOURCE.format(python=sys.executable), encoding="utf-8")
    log = tmp_path / "fake-engine.log"
    log.write_text("", encoding="utf-8")
    monkeypatch.setenv("MUCARO_FAKE_ENGINE_LOG", str(log))
    monkeypatch.setenv("MUCARO_FAKE_ENGINE_MODE", mode)
    monkeypatch.setattr(coding_container, "resolve_container_engine", lambda preferred=None: engine)
    real_popen = subprocess.Popen
    real_run = subprocess.run

    def route(argv):
        if argv and argv[0] == engine:
            return [sys.executable, str(script), *argv[1:]]
        return argv

    class RoutedPopen(real_popen):  # type: ignore[type-arg]
        def __init__(self, argv, *args, **kwargs):
            super().__init__(route(argv), *args, **kwargs)

    monkeypatch.setattr(coding_container.subprocess, "Popen", RoutedPopen)
    monkeypatch.setattr(coding_container.subprocess, "run", lambda argv, *args, **kwargs: real_run(route(argv), *args, **kwargs))
    return engine, log


@pytest.mark.parametrize("mode,stream", [("flood_stdout", "stdout"), ("flood_stderr", "stderr")])
def test_runaway_output_is_stopped_at_the_cap_with_bounded_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, stream: str
) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    _, log = real_fake_engine(tmp_path, monkeypatch, mode)
    cap = 100_000
    plan = build_coding_container_plan(
        repo, image=PINNED, command=["yes"], cfg=cfg, max_output_bytes=cap, timeout_seconds=60
    )

    started = time.monotonic()
    with pytest.raises(CodingContainerError) as exc:
        run_coding_container_plan(plan, cfg=cfg)
    elapsed = time.monotonic() - started

    # The engine never exits on its own: it must be stopped by the cap, far before the 60 s timeout.
    assert exc.value.status == "ERROR_CODING_CONTAINER_OUTPUT_TOO_LARGE"
    assert elapsed < 30
    receipt = exc.value.receipt
    assert receipt is not None
    assert receipt["stop_reason"] == "ERROR_CODING_CONTAINER_OUTPUT_TOO_LARGE"
    captured = receipt[stream]
    assert captured["truncated"] is True
    assert captured["byte_size"] == cap
    assert captured["text"].endswith(coding_container.OUTPUT_TRUNCATION_MARKER.format(max_bytes=cap))
    assert len(captured["text"]) <= cap + 200
    # Reading stopped within one chunk of the cap instead of buffering the whole stream.
    assert cap < captured["observed_byte_size_at_stop"] <= cap + coding_container.OUTPUT_READ_CHUNK_BYTES
    other = receipt["stdout" if stream == "stderr" else "stderr"]
    assert other["truncated"] is False
    # The named container is force-removed, not left running.
    events = log.read_text(encoding="utf-8").splitlines()
    name = events[0].removeprefix("run --name=")
    assert name.startswith("mucaro-coding-") and name == receipt["container_name"]
    assert f"rm --force {name}" in events


def test_timeout_stops_and_removes_the_named_container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    _, log = real_fake_engine(tmp_path, monkeypatch, "hang")
    plan = build_coding_container_plan(repo, image=PINNED, command=["sleep", "600"], cfg=cfg, timeout_seconds=1)

    started = time.monotonic()
    with pytest.raises(CodingContainerError) as exc:
        run_coding_container_plan(plan, cfg=cfg)

    assert exc.value.status == "ERROR_CODING_CONTAINER_TIMEOUT"
    assert time.monotonic() - started < 30
    assert exc.value.receipt is not None
    assert exc.value.receipt["stdout"]["text"] == "started\n"
    assert exc.value.receipt["stdout"]["truncated"] is False
    events = log.read_text(encoding="utf-8").splitlines()
    assert f"rm --force {exc.value.receipt['container_name']}" in events


def test_bounded_runner_returns_complete_output_under_the_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    _, log = real_fake_engine(tmp_path, monkeypatch, "ok")
    plan = build_coding_container_plan(repo, image=PINNED, command=["pytest"], cfg=cfg, max_output_bytes=1000)

    receipt = run_coding_container_plan(plan, cfg=cfg)

    assert receipt["execution_status"] == "FAIL"
    assert receipt["exit_code"] == 3
    assert receipt["stdout"]["byte_size"] == 1000
    assert receipt["stdout"]["truncated"] is False
    assert receipt["stderr"]["text"] == "warn\n"
    assert not any(line.startswith("rm ") for line in log.read_text(encoding="utf-8").splitlines())
