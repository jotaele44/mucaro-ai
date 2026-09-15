from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from backend.app.coding_container import (
    CodingContainerError,
    build_coding_container_command,
    build_coding_container_plan,
)
from backend.app.config import Settings

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required for coding container tests")
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


def test_read_only_plan_has_fail_closed_container_defaults(tmp_path: Path, monkeypatch) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    monkeypatch.setattr("backend.app.coding_container.resolve_container_engine", lambda preferred=None: "/usr/bin/docker")

    plan = build_coding_container_plan(repo, image=PINNED, command=["python", "-m", "pytest", "-q"], cfg=cfg)
    argv = build_coding_container_command(plan)

    assert plan.mount_mode == "read_only"
    assert plan.network_allowed is False
    assert "--read-only" in argv
    assert "--cap-drop=ALL" in argv
    assert "--security-opt=no-new-privileges" in argv
    assert "--network=none" in argv
    assert "--workdir=/workspace/repo" in argv
    assert any(token.startswith("--mount=type=bind,src=") and token.endswith(",ro") for token in argv)
    assert "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=256m" in argv
    assert "--tmpfs=/workspace/out:rw,nosuid,nodev,size=256m" in argv
    assert PINNED in argv
    assert plan.plan_sha256 == plan.plan_sha256


def test_read_write_mount_is_explicit_and_network_stays_denied(tmp_path: Path, monkeypatch) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    monkeypatch.setattr("backend.app.coding_container.resolve_container_engine", lambda preferred=None: "podman")

    plan = build_coding_container_plan(
        repo,
        image=PINNED,
        command=["python", "-c", "print('ok')"],
        cfg=cfg,
        mount_mode="read_write",
    )
    argv = build_coding_container_command(plan)

    assert plan.mount_mode == "read_write"
    assert "--network=none" in argv
    assert any(token.startswith("--mount=type=bind,src=") and token.endswith(",rw") for token in argv)


def test_network_enablement_and_unpinned_images_fail_closed(tmp_path: Path, monkeypatch) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    monkeypatch.setattr("backend.app.coding_container.resolve_container_engine", lambda preferred=None: "docker")

    with pytest.raises(CodingContainerError) as network:
        build_coding_container_plan(repo, image=PINNED, command=["pytest"], cfg=cfg, network_allowed=True)
    assert network.value.status == "ERROR_CODING_CONTAINER_NETWORK_POLICY"

    with pytest.raises(CodingContainerError) as image:
        build_coding_container_plan(repo, image="ghcr.io/jotaele44/mucaro-coding:latest", command=["pytest"], cfg=cfg)
    assert image.value.status == "ERROR_CONTAINER_IMAGE_NOT_PINNED"


def test_command_and_resource_limits_fail_closed(tmp_path: Path, monkeypatch) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    monkeypatch.setattr("backend.app.coding_container.resolve_container_engine", lambda preferred=None: "docker")

    with pytest.raises(CodingContainerError) as empty:
        build_coding_container_plan(repo, image=PINNED, command=[], cfg=cfg)
    assert empty.value.status == "ERROR_CODING_CONTAINER_COMMAND"

    with pytest.raises(CodingContainerError) as nul:
        build_coding_container_plan(repo, image=PINNED, command=["python\x00bad"], cfg=cfg)
    assert nul.value.status == "ERROR_CODING_CONTAINER_COMMAND"

    with pytest.raises(CodingContainerError) as limit:
        build_coding_container_plan(repo, image=PINNED, command=["pytest"], cfg=cfg, memory_mb=0)
    assert limit.value.status == "ERROR_CODING_CONTAINER_LIMIT"
