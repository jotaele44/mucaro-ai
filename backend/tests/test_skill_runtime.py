from pathlib import Path

import pytest

from backend.app.capability_policy import CapabilityPolicy
from backend.app.artifact_identity import list_artifacts, trace_ancestors
from backend.app.config import Settings
from backend.app.skill_runtime import (
    SkillDefinition,
    SkillRuntimeError,
    activate_skill,
    deactivate_skill,
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


def make_app_store_settings(root: Path) -> Settings:
    cfg = make_settings(root)
    return Settings(**{**cfg.__dict__, "distribution_channel": "mac_app_store"})


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
    assert activated["policy_decision"]["status"] == "AUTHORIZED"
    assert activated["policy_decision"]["requested"] == ["runtime.in_process"]

    executed = execute_skill(skill_id, cfg=cfg, executed_by="test")
    assert executed["status"] == "SKILL_EXECUTED"
    assert executed["execution"]["status"] == "PASS"
    assert executed["execution"]["execution_contract"]["status"] == "PASS"
    assert executed["execution"]["execution_contract"]["execution_id"] == executed["execution"]["execution_id"]
    assert executed["execution"]["policy_decision"]["status"] == "AUTHORIZED"
    assert executed["result"]["repo_id"] == "mucaro-ai"
    assert executed["result"]["membership_status"] == "PROVISIONAL"
    assert executed["result"]["baseline_required"] is False
    lineage = executed["execution"]["artifact_lineage"]
    assert lineage["input"]["artifact_type"] == "SOURCE"
    assert lineage["output"]["artifact_type"] == "DERIVED"
    assert lineage["output"]["parent_artifact_ids"] == [lineage["input"]["artifact_id"]]
    assert [row["artifact_id"] for row in trace_ancestors(cfg.processed_dir, lineage["output"]["artifact_id"])] == [
        lineage["input"]["artifact_id"]
    ]

    mounts = list_skill_mounts(cfg)["mounts"]
    executions = list_skill_executions(cfg)["executions"]
    assert len(mounts) == 1
    assert len(executions) == 1
    assert executions[0]["skill_digest"] == mounts[0]["digest"]
    assert len(list_artifacts(cfg.processed_dir)) == 2


def test_deactivation_revokes_persisted_grant_preserves_receipts_and_requires_reactivation(tmp_path: Path):
    cfg = make_settings(tmp_path)
    skill_id = "mucaro/intelligence-plane-health"
    mount = mount_skill(skill_id, cfg=cfg)["mount"]
    activate_skill(skill_id, cfg=cfg)
    previous = execute_skill(skill_id, cfg=cfg)["execution"]
    revoked = deactivate_skill(skill_id, cfg=cfg, deactivated_by="test")
    assert revoked["mount"]["mount_id"] == mount["mount_id"]
    assert revoked["mount"]["state"] == "INACTIVE"
    assert revoked["mount"]["policy_decision"] is None
    assert revoked["mount"]["lifecycle_events"][-1]["previous_policy_decision"]["status"] == "AUTHORIZED"
    assert previous["activation_binding"]["lifecycle_sequence"] < revoked["mount"]["lifecycle_sequence"]
    assert deactivate_skill(skill_id, cfg=cfg)["mount"] == revoked["mount"]
    # A new Settings instance reads the persisted revocation, not process-local state.
    restarted = make_settings(tmp_path)
    with pytest.raises(SkillRuntimeError, match=skill_id) as error:
        execute_skill(skill_id, cfg=restarted)
    assert error.value.status == "ERROR_SKILL_NOT_ACTIVE"
    assert list_skill_executions(restarted)["executions"] == [previous]
    activate_skill(skill_id, cfg=restarted)
    current = execute_skill(skill_id, cfg=restarted)["execution"]
    assert current["activation_binding"]["lifecycle_sequence"] > revoked["mount"]["lifecycle_sequence"]
    assert len(list_skill_executions(restarted)["executions"]) == 2


def test_revocation_remains_available_for_removed_skill_and_rejects_duplicate_mounts(monkeypatch, tmp_path: Path):
    from backend.app import skill_runtime

    cfg = make_settings(tmp_path)
    skill_id = "mucaro/intelligence-plane-health"
    mount_skill(skill_id, cfg=cfg)
    activate_skill(skill_id, cfg=cfg)
    monkeypatch.delitem(skill_runtime.SKILL_REGISTRY, skill_id)
    assert deactivate_skill(skill_id, cfg=cfg)["status"] == "SKILL_DEACTIVATED"
    with pytest.raises(SkillRuntimeError) as missing:
        deactivate_skill("missing/skill", cfg=cfg)
    assert missing.value.status == "ERROR_SKILL_NOT_MOUNTED"
    mounts = skill_runtime._load_mounts(cfg)
    skill_runtime._write_mounts([*mounts, mounts[0]], cfg)
    with pytest.raises(SkillRuntimeError) as ambiguous:
        deactivate_skill(skill_id, cfg=cfg)
    assert ambiguous.value.status == "ERROR_SKILL_MOUNT_AMBIGUOUS"


def test_deactivation_does_not_erase_or_relabel_already_admitted_execution(monkeypatch, tmp_path: Path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from backend.app import skill_runtime

    cfg = make_settings(tmp_path)
    entered, release = Event(), Event()

    def slow_handler(arguments, settings):
        entered.set()
        assert release.wait(5), "test handler was not released"
        return {"status": "FINISHED_AFTER_REVOCATION"}

    definition = SkillDefinition("test/inflight", "1", "Admission test", "IN_PROCESS", (), "test.in/1", "test.out/1", slow_handler)
    monkeypatch.setitem(skill_runtime.SKILL_REGISTRY, definition.skill_id, definition)
    mount_skill(definition.skill_id, cfg=cfg)
    activate_skill(definition.skill_id, cfg=cfg)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(execute_skill, definition.skill_id, cfg=cfg)
        try:
            assert entered.wait(5)
            revoked = deactivate_skill(definition.skill_id, cfg=cfg)["mount"]
            with pytest.raises(SkillRuntimeError) as blocked:
                execute_skill(definition.skill_id, cfg=cfg)
            assert blocked.value.status == "ERROR_SKILL_NOT_ACTIVE"
        finally:
            release.set()
        result = future.result(timeout=5)
    assert result["result"]["status"] == "FINISHED_AFTER_REVOCATION"
    assert result["execution"]["activation_binding"]["lifecycle_sequence"] < revoked["lifecycle_sequence"]
    assert list_skill_mounts(cfg)["mounts"][0]["state"] == "INACTIVE"


def test_concurrent_skill_mounts_and_lifecycle_updates_conserve_rows(tmp_path: Path):
    from concurrent.futures import ThreadPoolExecutor

    cfg = make_settings(tmp_path)
    skill_ids = [row["skill_id"] for row in list_skills()["skills"]]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda sid: mount_skill(sid, cfg=cfg), skill_ids * 8))
        list(pool.map(lambda sid: activate_skill(sid, cfg=cfg), skill_ids * 8))
        list(pool.map(lambda sid: deactivate_skill(sid, cfg=cfg), skill_ids * 8))
    mounts = list_skill_mounts(cfg)["mounts"]
    assert len(mounts) == len(skill_ids)
    assert {row["skill_id"] for row in mounts} == set(skill_ids)
    assert all(row["state"] == "INACTIVE" and row["policy_decision"] is None for row in mounts)
    assert all(row["lifecycle_sequence"] == 2 for row in mounts)


@pytest.mark.parametrize("arguments", [[], "", False, 0])
def test_skill_execution_rejects_falsey_non_object_arguments(tmp_path: Path, arguments):
    with pytest.raises(SkillRuntimeError) as error:
        execute_skill("mucaro/intelligence-plane-health", arguments, cfg=make_settings(tmp_path))
    assert error.value.status == "ERROR_SKILL_ARGUMENTS_NOT_OBJECT"


def test_failed_skill_execution_keeps_input_and_failure_receipt_lineage(monkeypatch, tmp_path: Path):
    from backend.app import skill_runtime

    cfg = make_settings(tmp_path)
    definition = SkillDefinition(
        skill_id="test/failing-artifact-skill",
        version="1.0.0",
        description="Synthetic failure provenance test.",
        runtime="IN_PROCESS",
        capabilities=(),
        input_schema="test.input/1",
        output_schema="test.output/1",
        handler=lambda arguments, settings: (_ for _ in ()).throw(RuntimeError("expected failure")),
    )
    monkeypatch.setitem(skill_runtime.SKILL_REGISTRY, definition.skill_id, definition)
    mount_skill(definition.skill_id, cfg=cfg)
    activate_skill(definition.skill_id, cfg=cfg)

    try:
        execute_skill(definition.skill_id, {"request": "failure"}, cfg=cfg)
    except SkillRuntimeError as exc:
        assert exc.status == "ERROR_SKILL_EXECUTION_FAILED"
    else:
        raise AssertionError("expected skill execution failure")

    event = list_skill_executions(cfg)["executions"][0]
    lineage = event["artifact_lineage"]
    assert lineage["input"]["artifact_type"] == "SOURCE"
    assert lineage["output"] is None
    assert lineage["failure_receipt"]["artifact_type"] == "RECEIPT"
    assert lineage["failure_receipt"]["parent_artifact_ids"] == [lineage["input"]["artifact_id"]]


def test_app_store_channel_allows_only_reviewed_in_process_skill(monkeypatch, tmp_path: Path):
    from backend.app import skill_runtime

    cfg = make_app_store_settings(tmp_path)
    reviewed = "mucaro/intelligence-plane-health"
    mount_skill(reviewed, cfg=cfg)
    assert activate_skill(reviewed, cfg=cfg)["status"] == "SKILL_ACTIVATED"

    unreviewed = SkillDefinition(
        skill_id="third-party/unreviewed",
        version="1.0.0",
        description="Synthetic unreviewed in-process skill.",
        runtime="IN_PROCESS",
        capabilities=(),
        input_schema="test.input/1",
        output_schema="test.output/1",
        handler=lambda arguments, settings: {"status": "SHOULD_NOT_RUN"},
    )
    monkeypatch.setitem(skill_runtime.SKILL_REGISTRY, unreviewed.skill_id, unreviewed)
    mount_skill(unreviewed.skill_id, cfg=cfg)

    try:
        activate_skill(unreviewed.skill_id, cfg=cfg)
    except SkillRuntimeError as exc:
        assert exc.status == "ERROR_DISTRIBUTION_RUNTIME_POLICY"
    else:
        raise AssertionError("expected unreviewed App Store skill denial")


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
    assert set(activated["policy_decision"]["granted"]) == {"network.public_http", "runtime.in_process"}


def test_explicit_policy_can_deny_runtime_even_when_skill_capability_is_allowed(monkeypatch, tmp_path: Path):
    from backend.app import skill_runtime

    cfg = make_settings(tmp_path)
    skill_id = "test/runtime-policy-skill"

    def handler(arguments, cfg):
        del arguments, cfg
        return {"status": "OK"}

    definition = SkillDefinition(
        skill_id=skill_id,
        version="1.0.0",
        description="Synthetic runtime policy test.",
        runtime="IN_PROCESS",
        capabilities=("filesystem.read",),
        input_schema="test.input/1",
        output_schema="test.output/1",
        handler=handler,
    )
    monkeypatch.setitem(skill_runtime.SKILL_REGISTRY, skill_id, definition)
    mount_skill(skill_id, cfg=cfg)
    policy = CapabilityPolicy.build(
        policy_id="deny-in-process",
        allowed={"filesystem.read", "runtime.in_process"},
        denied={"runtime.in_process"},
    )

    try:
        activate_skill(skill_id, cfg=cfg, policy=policy)
    except SkillRuntimeError as exc:
        assert exc.status == "ERROR_SKILL_CAPABILITY_NOT_AUTHORIZED"
        assert "runtime.in_process" in exc.detail
    else:
        raise AssertionError("expected runtime authorization failure")


def test_active_mount_can_be_reauthorized_under_a_new_policy(tmp_path: Path):
    cfg = make_settings(tmp_path)
    skill_id = "mucaro/intelligence-plane-health"
    mount_skill(skill_id, cfg=cfg)
    first = activate_skill(skill_id, cfg=cfg)
    assert first["status"] == "SKILL_ACTIVATED"

    replacement_policy = CapabilityPolicy.build(
        policy_id="replacement-policy",
        allowed={"runtime.in_process"},
    )
    second = activate_skill(skill_id, cfg=cfg, policy=replacement_policy)
    assert second["status"] == "SKILL_REAUTHORIZED"
    assert second["policy_decision"]["policy_id"] == "replacement-policy"

    executed = execute_skill(skill_id, cfg=cfg)
    assert executed["execution"]["policy_decision"]["policy_id"] == "replacement-policy"

def _synthetic_skill(skill_id: str, capabilities: tuple[str, ...]) -> SkillDefinition:
    return SkillDefinition(
        skill_id=skill_id,
        version="1.0.0",
        description="Synthetic re-evaluation test.",
        runtime="IN_PROCESS",
        capabilities=capabilities,
        input_schema="test.input/1",
        output_schema="test.output/1",
        handler=lambda arguments, settings: {"status": "RAN"},
    )


def test_execute_re_evaluates_distribution_channel_changed_after_activation(monkeypatch, tmp_path: Path):
    from backend.app import skill_runtime

    local_cfg = make_settings(tmp_path)
    definition = _synthetic_skill("test/channel-flip", ())
    monkeypatch.setitem(skill_runtime.SKILL_REGISTRY, definition.skill_id, definition)
    mount_skill(definition.skill_id, cfg=local_cfg)
    activate_skill(definition.skill_id, cfg=local_cfg)
    assert execute_skill(definition.skill_id, cfg=local_cfg)["status"] == "SKILL_EXECUTED"

    try:
        execute_skill(definition.skill_id, cfg=make_app_store_settings(tmp_path))
    except SkillRuntimeError as exc:
        assert exc.status == "ERROR_DISTRIBUTION_RUNTIME_POLICY"
    else:
        raise AssertionError("expected execution-time distribution denial")


def test_execute_rejects_tampered_mount_grant(monkeypatch, tmp_path: Path):
    from backend.app import skill_runtime

    cfg = make_settings(tmp_path)
    definition = _synthetic_skill("test/tampered-grant", ("network.public_http",))
    monkeypatch.setitem(skill_runtime.SKILL_REGISTRY, definition.skill_id, definition)
    mount_skill(definition.skill_id, cfg=cfg)
    activate_skill(definition.skill_id, cfg=cfg, allowed_capabilities={"network.public_http"})

    mounts = skill_runtime._load_mounts(cfg)
    for mount in mounts:
        if mount["skill_id"] == definition.skill_id:
            mount["policy_decision"]["granted"] = ["runtime.in_process"]  # silently drops network.public_http
    skill_runtime._write_mounts(mounts, cfg)

    try:
        execute_skill(definition.skill_id, cfg=cfg)
    except SkillRuntimeError as exc:
        assert exc.status == "ERROR_SKILL_POLICY_STALE"
        assert "network.public_http" in exc.detail
    else:
        raise AssertionError("expected tampered grant to be refused at execution")
