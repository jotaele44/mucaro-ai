from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import backend.app.coding_readiness as readiness
from backend.app.config import Settings
from backend.app.container_runtime import ContainerRuntimeError
from backend.app.coding_readiness import build_coding_readiness


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
    proc = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


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


def write_manifest(root: Path) -> None:
    (root / "_architecture").mkdir()
    (root / "_architecture" / "coding_regression_manifest.json").write_text(
        '{"schema_version":"test","requirements":[{"id":"CODE-R01","class":"positive","gate":"status"}]}\n',
        encoding="utf-8",
    )


def test_readiness_is_blocked_without_engine_or_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    write_manifest(tmp_path)

    def no_engine(preferred: str | None = None) -> str:
        raise ContainerRuntimeError("ERROR_CODING_ENGINE_UNAVAILABLE", preferred or "docker,podman")

    monkeypatch.setattr(readiness, "resolve_container_engine", no_engine)
    report = build_coding_readiness(repo, cfg=cfg)

    assert report["status"] == "CODING_READINESS_BLOCKED"
    assert report["execution_status"] == "NOT_EXECUTED"
    assert report["blockers"] == ["CONTAINER_ENGINE", "CODING_IMAGE"]
    assert report["invariants"]["execution_not_claimed"] is True
    assert len(report["receipt_sha256"]) == 64


def test_readiness_ready_requires_all_runtime_identities(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    write_manifest(tmp_path)
    image = "registry.invalid/mucaro-coding@sha256:" + "a" * 64

    monkeypatch.setattr(readiness, "resolve_container_engine", lambda preferred=None: "/usr/bin/docker")
    report = build_coding_readiness(repo, image=image, cfg=cfg)

    assert report["status"] == "CODING_READINESS_READY"
    assert report["blockers"] == []
    assert report["runtime"]["network_allowed"] is False
    assert report["runtime"]["implicit_image_pull_allowed"] is False
    assert all(row["valid"] for row in report["identity"])


def test_readiness_rejects_mutable_image_even_when_engine_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    write_manifest(tmp_path)

    monkeypatch.setattr(readiness, "resolve_container_engine", lambda preferred=None: "/usr/bin/docker")
    report = build_coding_readiness(repo, image="registry.invalid/mucaro-coding:latest", cfg=cfg)

    assert report["status"] == "CODING_READINESS_BLOCKED"
    assert report["blockers"] == ["CODING_IMAGE"]
    assert report["runtime"]["image_error"]["status"] == "ERROR_CONTAINER_IMAGE_NOT_PINNED"
