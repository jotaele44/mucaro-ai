from pathlib import Path

from backend.app.capability_policy import CapabilityPolicy
from backend.app.artifact_identity import list_artifacts, trace_ancestors
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


def _exact_head_arguments() -> dict:
    candidate = "a155464486798b5e0bd6d6f623138b37982013c5"
    return {
        "baseline_sha": "916a4e3793550d0bf22e5be22a8c0bc0a7c17ffa",
        "candidate_sha": candidate,
        "observed_head_sha": candidate,
        "observed_remote_sha": candidate,
        "observed_pr_head_sha": candidate,
        "worktree_clean": True,
        "required_gates": ["CI", "SECURITY", "RUNTIME", "ADVERSARIAL"],
        "passed_gates": ["ADVERSARIAL", "RUNTIME", "SECURITY", "CI"],
        "unresolved_states": [],
        "superseded_shas": ["f19646924fc057fd69834257e85e14f7a06dcd03"],
        "manifest_sha256": "a" * 64,
        "evidence_sha256": "b" * 64,
    }


def test_exact_head_certification_skill_passes_only_closed_exact_identity(tmp_path: Path):
    cfg = make_settings(tmp_path)
    skill_id = "mucaro/exact-head-certification-audit"
    mount_skill(skill_id, cfg=cfg)
    activate_skill(skill_id, cfg=cfg)
    result = execute_skill(skill_id, _exact_head_arguments(), cfg=cfg)["result"]

    assert result["status"] == "EXACT_HEAD_CERTIFICATION_PASS"
    assert result["certification_state"] == "PASS"
    assert result["promotable"] is True
    assert result["fail_closed"] is True
    assert result["identity_match"] is True
    assert result["missing_gates"] == []
    assert result["unresolved_states"] == []
    assert result["reasons"] == []


def test_exact_head_certification_skill_fails_closed_on_sha_drift_and_gate_residue(tmp_path: Path):
    cfg = make_settings(tmp_path)
    skill_id = "mucaro/exact-head-certification-audit"
    mount_skill(skill_id, cfg=cfg)
    activate_skill(skill_id, cfg=cfg)
    arguments = _exact_head_arguments()
    arguments["observed_remote_sha"] = "0" * 40
    arguments["passed_gates"] = ["CI", "SECURITY"]
    arguments["unresolved_states"] = ["RUNTIME_OPEN"]
    result = execute_skill(skill_id, arguments, cfg=cfg)["result"]

    assert result["status"] == "EXACT_HEAD_CERTIFICATION_OPEN"
    assert result["certification_state"] == "OPEN"
    assert result["promotable"] is False
    assert result["identity_match"] is False
    assert result["missing_gates"] == ["ADVERSARIAL", "RUNTIME"]
    assert result["unresolved_states"] == ["RUNTIME_OPEN"]
    assert any("candidate SHA mismatch: remote" in reason for reason in result["reasons"])
    assert any("required gates not passed" in reason for reason in result["reasons"])
    assert any("unresolved certification residue" in reason for reason in result["reasons"])


def test_exact_head_certification_skill_rejects_superseded_candidate_and_bad_hashes(tmp_path: Path):
    cfg = make_settings(tmp_path)
    skill_id = "mucaro/exact-head-certification-audit"
    mount_skill(skill_id, cfg=cfg)
    activate_skill(skill_id, cfg=cfg)
    arguments = _exact_head_arguments()
    arguments["superseded_shas"] = [arguments["candidate_sha"]]
    arguments["manifest_sha256"] = "not-a-hash"
    result = execute_skill(skill_id, arguments, cfg=cfg)["result"]

    assert result["certification_state"] == "OPEN"
    assert result["promotable"] is False
    assert "candidate SHA is explicitly superseded" in result["reasons"]
    assert any("manifest_sha256" in reason for reason in result["reasons"])



def test_exact_head_certification_skill_rejects_null_empty_required_values(tmp_path: Path):
    cfg = make_settings(tmp_path)
    skill_id = "mucaro/exact-head-certification-audit"
    mount_skill(skill_id, cfg=cfg)
    activate_skill(skill_id, cfg=cfg)
    arguments = {
        "baseline_sha": None,
        "candidate_sha": None,
        "observed_head_sha": None,
        "observed_remote_sha": None,
        "observed_pr_head_sha": None,
        "worktree_clean": True,
        "required_gates": [],
        "passed_gates": [],
        "manifest_sha256": None,
        "evidence_sha256": None,
        "unresolved_states": [],
        "superseded_shas": [],
    }
    result = execute_skill(skill_id, arguments, cfg=cfg)["result"]

    assert result["certification_state"] == "OPEN"
    assert result["promotable"] is False
    assert result["identity_match"] is False
    assert "required_gates must be a nonempty string list" in result["reasons"]
    assert any("baseline_sha" in reason for reason in result["reasons"])
    assert any("candidate_sha" in reason for reason in result["reasons"])
    assert any("manifest_sha256" in reason for reason in result["reasons"])
    assert any("evidence_sha256" in reason for reason in result["reasons"])


def test_exact_head_certification_skill_normalizes_sha_case_for_supersession(tmp_path: Path):
    cfg = make_settings(tmp_path)
    skill_id = "mucaro/exact-head-certification-audit"
    mount_skill(skill_id, cfg=cfg)
    activate_skill(skill_id, cfg=cfg)
    arguments = _exact_head_arguments()
    upper = arguments["candidate_sha"].upper()
    arguments["candidate_sha"] = upper
    arguments["observed_head_sha"] = upper
    arguments["observed_remote_sha"] = upper
    arguments["observed_pr_head_sha"] = upper
    arguments["superseded_shas"] = [upper.lower()]
    result = execute_skill(skill_id, arguments, cfg=cfg)["result"]

    assert result["certification_state"] == "OPEN"
    assert result["promotable"] is False
    assert result["candidate_sha"] == upper.lower()
    assert result["identity_match"] is True
    assert "candidate SHA is explicitly superseded" in result["reasons"]
