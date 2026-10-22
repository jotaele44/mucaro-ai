from __future__ import annotations

import hashlib
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from backend.app import agent_consumers as c
from backend.app import learning
from backend.app.main import create_app
from backend.app.tools import approve_tool_call, list_tool_calls, request_tool_call, verify_tool_audit_chain
from backend.tests.test_api import make_settings
from backend.tests.test_learning import verified


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture
def context(tmp_path):
    cfg = make_settings(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "file.txt").write_text("original\n")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Initial")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "file.txt").write_text("original\nchanged\n")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Change")
    skill = verified(cfg, scope="repository_review")
    binding = c.bind(cfg, agent_id="mucaro/agent/repository-review", candidate_id=skill["candidate_id"],
                     candidate_version=skill["version"], artifact_sha256=skill["artifact"]["sha256"],
                     recipe_id="evidence-review/1", approved=True)
    args = {"binding_id": binding["binding_id"], "repo_root": str(repo), "head_sha": git(repo, "rev-parse", "HEAD"),
            "base_sha": base, "file_path": "file.txt", "file_sha256": hashlib.sha256((repo / "file.txt").read_bytes()).hexdigest(),
            "idempotency_key": "task-1"}
    return cfg, repo, skill, binding, args


def run(cfg, args):
    planned = c.prepare(cfg, **args)
    return c.execute(cfg, planned["run_id"], version=planned["version"], plan_sha256=planned["plan_sha256"], approved=True)


def test_real_tool_execution_receipts_reward_and_idempotent_replay(context):
    cfg, repo, skill, _binding, args = context
    planned = c.prepare(cfg, **args)
    assert list_tool_calls(cfg)["tool_calls"] == []
    with pytest.raises(learning.LearningError, match="APPROVAL_REQUIRED"):
        c.execute(cfg, planned["run_id"], version=1, plan_sha256=planned["plan_sha256"], approved=False)
    completed = run(cfg, args)
    assert completed["state"] == "COMPLETED", completed
    assert len(completed["results"]) == 4
    assert completed["results"][2]["result"]["sha256"] == args["file_sha256"]
    assert verify_tool_audit_chain(cfg)["status"] == "TOOL_AUDIT_CHAIN_OK"
    assert run(cfg, args) == completed
    assert len(list_tool_calls(cfg)["tool_calls"]) == 4
    feedback = c.reward(cfg, completed["run_id"], output_sha256=completed["output_sha256"], score=1.0,
                        criteria="Exact HEAD, base, bytes and clean-source evidence independently verified", permitted_use=True)
    assert feedback["candidate_id"] == skill["candidate_id"]
    assert learning.recommend(cfg, "repository_review")["top_candidates"] == [skill["candidate_id"]]
    assert git(repo, "status", "--porcelain") == ""


def test_stale_plan_and_conflicting_replay_do_not_create_calls(context):
    cfg, _, _, _, args = context
    row = c.prepare(cfg, **args)
    with pytest.raises(learning.LearningError, match="APPROVAL_STALE"):
        c.execute(cfg, row["run_id"], version=1, plan_sha256="0" * 64, approved=True)
    with pytest.raises(learning.LearningError, match="REPLAY_CONFLICT"):
        c.prepare(cfg, **{**args, "file_path": "other.txt"})
    assert not list_tool_calls(cfg)["tool_calls"]


@pytest.mark.parametrize("mutation,reason", [("head", "HEAD_CHANGED"), ("dirty", "DIRTY_SOURCE"), ("filehash", "FILE_CHANGED")])
def test_source_identity_falsifiers_fail_closed(context, mutation, reason):
    cfg, repo, _, _, args = context
    if mutation == "head":
        args["head_sha"] = "0" * 40
    if mutation == "dirty":
        (repo / "file.txt").write_text("unauthorized change")
    if mutation == "filehash":
        args["file_sha256"] = "0" * 64
    result = run(cfg, args)
    assert result["state"] == "FAILED"
    assert reason in result["error"]["detail"]
    with pytest.raises(learning.LearningError, match="OUTPUT_STALE"):
        c.reward(cfg, result["run_id"], output_sha256="0" * 64, score=1.0, criteria="not valid", permitted_use=True)


def test_binding_scope_and_artifact_checks(context):
    cfg, _, skill, _, _ = context
    kw = {"agent_id": "mucaro/agent/release-audit", "candidate_id": skill["candidate_id"], "candidate_version": skill["version"],
              "artifact_sha256": skill["artifact"]["sha256"], "recipe_id": "summary/1", "approved": True}
    with pytest.raises(learning.LearningError, match="SCOPE_MISMATCH"):
        c.bind(cfg, **kw)
    with pytest.raises(learning.LearningError, match="ARTIFACT_STALE"):
        c.bind(cfg, **{**kw, "agent_id": "mucaro/agent/repository-review", "artifact_sha256": "0" * 64})
    with pytest.raises(learning.LearningError, match="UNKNOWN"):
        c.bind(cfg, **{**kw, "recipe_id": "shell/1"})


def test_skill_rollback_or_binding_revocation_blocks_prepared_execution(context):
    cfg, _, skill, binding, args = context
    row = c.prepare(cfg, **args)
    learning.rollback(cfg, skill["candidate_id"], version=skill["version"], reason="Evidence invalidated")
    with pytest.raises(learning.LearningError, match="SKILL_REVOKED"):
        c.execute(cfg, row["run_id"], version=1, plan_sha256=row["plan_sha256"], approved=True)
    c.unbind(cfg, binding["binding_id"], version=1, reason="Retire consumer binding")
    with pytest.raises(learning.LearningError, match="BINDING_NOT_ACTIVE"):
        c.prepare(cfg, **{**args, "idempotency_key": "new-task"})
    assert not list_tool_calls(cfg)["tool_calls"]


def test_directory_permission_is_not_granted_by_binding(context, tmp_path):
    cfg, _, _, _, args = context
    outside = replace(cfg, project_root=tmp_path / "different-project")
    result = run(outside, args)
    assert result["state"] == "FAILED"
    calls = list_tool_calls(outside)["tool_calls"]
    assert len(calls) == 1 and calls[0]["status"] == "FAILED"
    assert "PERMISSION" in str(calls[0]["result"])


def test_only_one_concurrent_execution_can_claim_a_plan(context):
    cfg, _, _, _, args = context
    plan = c.prepare(cfg, **args)
    def execute(_):
        try:
            return c.execute(cfg, plan["run_id"], version=1, plan_sha256=plan["plan_sha256"], approved=True)["state"]
        except learning.LearningError:
            return "REFUSED"
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(execute, range(3)))
    assert "COMPLETED" in results
    assert len(list_tool_calls(cfg)["tool_calls"]) == 4


def test_interruption_retains_calls_cancels_unstarted_and_never_reexecutes(context):
    cfg, _, _, _, args = context
    row = c.prepare(cfg, **args)
    with learning._store(cfg) as state:
        r = state["consumer_runs"][row["run_id"]]
        r.update(state="RUNNING", version=2)
        learning._receipt(state, "RECOVERY_TEST", None, {"run_id": row["run_id"]})
    call = request_tool_call("coding.repo_status", {"repo_root": args["repo_root"]}, cfg=cfg, run_id=row["run_id"])["tool_call"]
    approve_tool_call(call["tool_call_id"], cfg=cfg)
    stopped = c.stop(cfg, row["run_id"], version=2, reason="Worker process was lost; do not retry")
    assert stopped["state"] == "INTERRUPTED"
    assert stopped["interrupted_tool_calls"][0]["status"] == "CANCELLED"
    assert run(cfg, args)["state"] == "INTERRUPTED"
    assert len(list_tool_calls(cfg)["tool_calls"]) == 1


def test_cancel_prepared_and_wrong_output_hash(context):
    cfg, _, _, _, args = context
    row = c.prepare(cfg, **args)
    assert c.stop(cfg, row["run_id"], version=1, reason="Cancel plan")["state"] == "CANCELLED"
    assert run(cfg, args)["state"] == "CANCELLED"
    good = run(cfg, {**args, "idempotency_key": "other"})
    with pytest.raises(learning.LearningError, match="OUTPUT_STALE"):
        c.reward(cfg, good["run_id"], output_sha256="0" * 64, score=1.0, criteria="wrong output", permitted_use=True)


def test_api_uses_existing_auth_and_rejects_unknown_fields(context):
    cfg, _, _, _, args = context
    client = TestClient(create_app(replace(cfg, operator_token="operator-token")))
    assert client.get("/learning/consumers").status_code == 401
    headers = {"X-Mucaro-Token": "operator-token"}
    assert client.get("/learning/consumers", headers=headers).status_code == 200
    assert client.post("/learning/consumer-runs/prepare", json={**args, "shell": "anything"}, headers=headers).status_code == 422
    plan = client.post("/learning/consumer-runs/prepare", json=args, headers=headers).json()
    response = client.post(f'/learning/consumer-runs/{plan["run_id"]}/execute', headers=headers,
                           json={"version": 1, "plan_sha256": plan["plan_sha256"], "approved": True})
    assert response.status_code == 200 and response.json()["state"] == "COMPLETED"


def test_implementation_and_tool_authority_drift_are_not_execution_authority(context, monkeypatch):
    cfg, _, _, _, args = context
    row = c.prepare(cfg, **args)
    original_digest = c._implementation_sha256
    monkeypatch.setattr(c, "_implementation_sha256", lambda: "0" * 64)
    with pytest.raises(learning.LearningError, match="IMPLEMENTATION_STALE"):
        c.execute(cfg, row["run_id"], version=1, plan_sha256=row["plan_sha256"], approved=True)
    monkeypatch.setattr(c, "_implementation_sha256", original_digest)
    definition = c.TOOL_REGISTRY["coding.repo_status"]
    monkeypatch.setitem(c.TOOL_REGISTRY, "coding.repo_status", replace(definition, network="remote"))
    result = c.execute(cfg, row["run_id"], version=1, plan_sha256=row["plan_sha256"], approved=True)
    assert result["state"] == "FAILED" and "AUTHORITY_DRIFT" in result["error"]["detail"]
    assert not list_tool_calls(cfg)["tool_calls"]


def test_physical_root_replacement_is_rejected_before_a_tool_runs(context):
    import shutil
    cfg, repo, _, _, args = context
    row = c.prepare(cfg, **args)
    relocated = repo.with_name("retained-original")
    repo.rename(relocated)
    shutil.copytree(relocated, repo)
    result = c.execute(cfg, row["run_id"], version=1, plan_sha256=row["plan_sha256"], approved=True)
    assert result["state"] == "FAILED" and "ROOT_CHANGED" in result["error"]["detail"]
    assert not list_tool_calls(cfg)["tool_calls"]


def test_midrun_failure_keeps_passed_results_and_cancels_unstarted_call(context, monkeypatch):
    cfg, _, _, _, args = context
    original = c.run_tool_call
    count = 0
    def interrupted(*a, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("Injected boundary failure before second execution")
        return original(*a, **kwargs)
    monkeypatch.setattr(c, "run_tool_call", interrupted)
    row = run(cfg, args)
    assert row["state"] == "FAILED" and len(row["results"]) == 1
    calls = list_tool_calls(cfg)["tool_calls"]
    assert [call["status"] for call in calls] == ["EXECUTED", "CANCELLED"]
    assert run(cfg, args) == row
    assert len(list_tool_calls(cfg)["tool_calls"]) == 2


def test_revoked_binding_disallows_new_reward_and_path_traversal(context):
    cfg, _, _, binding, args = context
    with pytest.raises(learning.LearningError, match="FILE_PATH_INVALID"):
        c.prepare(cfg, **{**args, "file_path": "../outside.txt"})
    row = run(cfg, args)
    c.unbind(cfg, binding["binding_id"], version=1, reason="Withdraw scope")
    with pytest.raises(learning.LearningError, match="BINDING_NOT_ACTIVE"):
        c.reward(cfg, row["run_id"], output_sha256=row["output_sha256"], score=1.0, criteria="Stale binding", permitted_use=True)
