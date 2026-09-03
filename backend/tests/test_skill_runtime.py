from pathlib import Path

from backend.app.config import Settings
from backend.app.skill_runtime import (
    SkillDefinition,
    SkillRuntimeError,
    activate_skill,
    execute_skill,
    list_skill_executions,
    list_skill_mounts,
    list_skills,
    mount_skill,
)


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


def test_registered_skill_has_stable_digest_and_contract_metadata():
    payload = list_skills()
    assert payload["status"] == "SKILLS_OK"
    skill = next(item for item in payload["skills"] if item["skill_id"] == "mucaro/intelligence-plane-health")
    assert skill["version"] == "1.0.0"
    assert skill["runtime"] == "IN_PROCESS"
    assert skill["digest"].startswith("sha256:")
    assert skill["capabilities"] == []


def test_skill_must_mount_then_activate_before_execution(tmp_path: Path):
    cfg = make_settings(tmp_path)
    skill_id = "mucaro/intelligence-plane-health"

    try:
        execute_skill(skill_id, cfg=cfg)
    except SkillRuntimeError as exc:
        assert exc.status == "ERROR_SKILL_NOT_ACTIVE"
    else:
        raise AssertionError("expected inactive skill failure")

    mounted = mount_skill(skill_id, cfg=cfg, mounted_by="test")
    assert mounted["mount"]["state"] == "MOUNTED"

    try:
        execute_skill(skill_id, cfg=cfg)
    except SkillRuntimeError as exc:
        assert exc.status == "ERROR_SKILL_NOT_ACTIVE"
    else:
        raise AssertionError("expected inactive skill failure")

    activated = activate_skill(skill_id, cfg=cfg, activated_by="test")
    assert activated["mount"]["state"] == "ACTIVE"

    executed = execute_skill(skill_id, cfg=cfg, executed_by="test")
    assert executed["status"] == "SKILL_EXECUTED"
    assert executed["execution"]["status"] == "PASS"
    assert executed["result"]["repo_id"] == "mucaro-ai"
    assert executed["result"]["membership_status"] == "PROVISIONAL"
    assert executed["result"]["baseline_required"] is False

    mounts = list_skill_mounts(cfg)["mounts"]
    executions = list_skill_executions(cfg)["executions"]
    assert len(mounts) == 1
    assert len(executions) == 1
    assert executions[0]["skill_digest"] == mounts[0]["digest"]


def test_mount_is_idempotent_for_same_digest(tmp_path: Path):
    cfg = make_settings(tmp_path)
    skill_id = "mucaro/intelligence-plane-health"
    first = mount_skill(skill_id, cfg=cfg)
    second = mount_skill(skill_id, cfg=cfg)

    assert first["mount"]["mount_id"] == second["mount"]["mount_id"]
    assert second["status"] == "SKILL_ALREADY_MOUNTED"


def test_unknown_skill_fails_closed(tmp_path: Path):
    cfg = make_settings(tmp_path)
    try:
        mount_skill("unknown/skill", cfg=cfg)
    except SkillRuntimeError as exc:
        assert exc.status == "ERROR_SKILL_NOT_REGISTERED"
    else:
        raise AssertionError("expected unknown skill failure")


def test_capability_authorization_fails_closed(monkeypatch, tmp_path: Path):
    from backend.app import skill_runtime

    cfg = make_settings(tmp_path)
    skill_id = "test/network-skill"

    def handler(arguments, cfg):
        del arguments, cfg
        return {"status": "OK"}

    definition = SkillDefinition(
        skill_id=skill_id,
        version="1.0.0",
        description="Synthetic capability test.",
        runtime="IN_PROCESS",
        capabilities=("network.public_http",),
        input_schema="test.input/1",
        output_schema="test.output/1",
        handler=handler,
    )
    monkeypatch.setitem(skill_runtime.SKILL_REGISTRY, skill_id, definition)
    mount_skill(skill_id, cfg=cfg)

    try:
        activate_skill(skill_id, cfg=cfg, allowed_capabilities=set())
    except SkillRuntimeError as exc:
        assert exc.status == "ERROR_SKILL_CAPABILITY_NOT_AUTHORIZED"
        assert "network.public_http" in exc.detail
    else:
        raise AssertionError("expected capability authorization failure")

    activated = activate_skill(skill_id, cfg=cfg, allowed_capabilities={"network.public_http"})
    assert activated["mount"]["state"] == "ACTIVE"
