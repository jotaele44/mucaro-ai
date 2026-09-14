from __future__ import annotations

from pathlib import Path

from backend.app.config import Settings
from backend.app.runtime_identity import RUNTIME_IDENTITY_SCHEMA_VERSION, build_runtime_identity
from backend.app.skill_runtime import activate_skill, execute_skill, mount_skill


def make_settings(root: Path) -> Settings:
    return Settings(
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


def test_runtime_identity_is_deterministic_for_same_environment(monkeypatch, tmp_path: Path) -> None:
    from backend.app import runtime_identity

    monkeypatch.setattr(runtime_identity, "current_git_head", lambda project_root: "a" * 40)
    first = build_runtime_identity(
        project_root=tmp_path,
        runtime="IN_PROCESS",
        skill_id="test/skill",
        skill_version="1.0.0",
        skill_digest="sha256:test",
        packages=(),
    )
    second = build_runtime_identity(
        project_root=tmp_path,
        runtime="IN_PROCESS",
        skill_id="test/skill",
        skill_version="1.0.0",
        skill_digest="sha256:test",
        packages=(),
    )
    assert first == second
    assert first["schema_version"] == RUNTIME_IDENTITY_SCHEMA_VERSION
    assert first["git_head"] == "a" * 40
    assert len(first["runtime_identity_sha256"]) == 64


def test_skill_execution_persists_runtime_identity(monkeypatch, tmp_path: Path) -> None:
    from backend.app import runtime_identity

    monkeypatch.setattr(runtime_identity, "current_git_head", lambda project_root: "b" * 40)
    cfg = make_settings(tmp_path)
    skill_id = "mucaro/intelligence-plane-health"
    mount_skill(skill_id, cfg=cfg)
    activate_skill(skill_id, cfg=cfg)
    result = execute_skill(skill_id, cfg=cfg)
    identity = result["execution"]["runtime_identity"]
    assert identity["schema_version"] == RUNTIME_IDENTITY_SCHEMA_VERSION
    assert identity["runtime"] == "IN_PROCESS"
    assert identity["skill_id"] == skill_id
    assert identity["git_head"] == "b" * 40
    assert identity["skill_digest"] == result["execution"]["skill_digest"]
