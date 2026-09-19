from __future__ import annotations

import subprocess
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
    real_run = subprocess.run

    def routed_run(argv, *args, **kwargs):
        if argv and argv[0] == "/usr/bin/docker":
            return subprocess.CompletedProcess(argv, 0, stdout=b"2 passed\n", stderr=b"")
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(coding_container.subprocess, "run", routed_run)
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
    real_run = subprocess.run

    def routed_run(argv, *args, **kwargs):
        if argv and argv[0] == "/usr/bin/docker":
            return subprocess.CompletedProcess(argv, 7, stdout=b"", stderr=b"failing test\n")
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(coding_container.subprocess, "run", routed_run)
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
    real_run = subprocess.run
    mutated = False

    def routed_run(argv, *args, **kwargs):
        nonlocal mutated
        if argv and argv[0] == "/usr/bin/docker":
            if not mutated:
                (repo / "tracked.txt").write_text("tampered\n", encoding="utf-8")
                mutated = True
            return subprocess.CompletedProcess(argv, 0, stdout=b"ok\n", stderr=b"")
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(coding_container.subprocess, "run", routed_run)
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
    real_run = subprocess.run

    def routed_run(argv, *args, **kwargs):
        if argv and argv[0] == "/usr/bin/docker":
            return subprocess.CompletedProcess(argv, 0, stdout=b"12345", stderr=b"")
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(coding_container.subprocess, "run", routed_run)
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
