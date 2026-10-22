"""Bounded learned-workflow agents over the existing approval-gated tool runtime.

Prose never becomes executable code. Operators bind verified artifacts to fixed
recipes and approve each exact run plan. Persistent runs fail closed on ambiguity,
revocation, stale source, tool failure, or interrupted execution.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from . import learning
from .config import Settings
from .tools import (
    TOOL_REGISTRY,
    ToolRuntimeError,
    approve_tool_call,
    cancel_tool_call,
    list_tool_calls,
    request_tool_call,
    run_tool_call,
)
from .utils import utc_now

AGENTS = {
    "mucaro/agent/repository-review": {"label": "Repository review", "scope": "repository_review"},
    "mucaro/agent/release-audit": {"label": "Release evidence review", "scope": "release_audit"},
}
RECIPES = {
    "summary/1": {"label": "Repository summary", "tools": ["coding.repo_status"]},
    "evidence-review/1": {"label": "Source and comparison evidence", "tools": [
        "coding.repo_status", "coding.diff", "coding.read_file", "coding.repo_status"]},
}
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"}
_check = learning._check


def _binding(state: dict, binding_id: str) -> dict:
    row = state.get("consumer_bindings", {}).get(binding_id)
    _check(row is not None and row["enabled"], "CONSUMER_BINDING_NOT_ACTIVE")
    candidate = state["candidates"].get(row["candidate_id"])
    _check(candidate is not None and candidate["state"] == "VERIFIED", "CONSUMER_SKILL_REVOKED")
    _check(candidate["artifact"]["sha256"] == row["artifact_sha256"] and candidate["version"] == row["candidate_version"],
           "CONSUMER_ARTIFACT_STALE")
    _check(row["agent_id"] in AGENTS and row["scope"] == AGENTS[row["agent_id"]]["scope"], "CONSUMER_SCOPE_MISMATCH")
    _check(row["recipe_sha256"] == learning.digest(RECIPES.get(row["recipe_id"])), "CONSUMER_RECIPE_STALE")
    return row


def listing(cfg: Settings) -> dict:
    state = learning.snapshot(cfg)
    return {"schema": "mucaro-agent-consumers/1", "status": "CONSUMERS_OK", "agents": AGENTS, "recipes": RECIPES,
            "bindings": state.get("consumer_bindings", {}), "runs": state.get("consumer_runs", {}),
            "limitations": ["Deterministic read-only workflow agents; no autonomous model or arbitrary-code training.",
                            "Binding a procedure does not authorize a run or grant directory access."]}


def bind(cfg: Settings, *, agent_id: str, candidate_id: str, candidate_version: int,
         artifact_sha256: str, recipe_id: str, approved: bool) -> dict:
    _check(approved is True, "CONSUMER_BINDING_APPROVAL_REQUIRED")
    _check(agent_id in AGENTS and recipe_id in RECIPES, "CONSUMER_OR_RECIPE_UNKNOWN")
    with learning._store(cfg) as state:
        row = state["candidates"].get(candidate_id)
        _check(row is not None and row["state"] == "VERIFIED" and row["kind"] == "skill", "VERIFIED_SKILL_REQUIRED")
        _check(type(candidate_version) is int and row["version"] == candidate_version, "STALE_VERSION")
        _check(row["artifact"]["sha256"] == artifact_sha256, "CONSUMER_ARTIFACT_STALE")
        _check(row["scope"] == AGENTS[agent_id]["scope"], "CONSUMER_SCOPE_MISMATCH")
        body = {"schema": "mucaro-consumer-binding/1", "agent_id": agent_id, "candidate_id": candidate_id, "candidate_version": candidate_version,
                "artifact_sha256": artifact_sha256, "recipe_id": recipe_id,
                "recipe_sha256": learning.digest(RECIPES[recipe_id]), "scope": row["scope"]}
        key = "binding_" + learning.digest(body)
        bindings = state.setdefault("consumer_bindings", {})
        if key in bindings:
            _check(bindings[key]["enabled"], "CONSUMER_BINDING_REVOKED")
            return bindings[key]
        result = {**body, "binding_id": key, "version": 1, "enabled": True, "approved_by": "local_operator"}
        bindings[key] = result
        learning._receipt(state, "CONSUMER_BOUND", candidate_id, result)
        return result


def unbind(cfg: Settings, binding_id: str, *, version: int, reason: str) -> dict:
    _check(bool(reason.strip()), "CONSUMER_REVOCATION_REASON_REQUIRED")
    with learning._store(cfg) as state:
        row = state.get("consumer_bindings", {}).get(binding_id)
        _check(row is not None and row["enabled"], "CONSUMER_BINDING_NOT_ACTIVE")
        _check(type(version) is int and row["version"] == version, "STALE_VERSION")
        row.update(enabled=False, version=version + 1)
        learning._receipt(state, "CONSUMER_UNBOUND", row["candidate_id"], {"binding_id": binding_id, "reason": reason})
        return row


def _validate_inputs(repo_root: str, head_sha: str, base_sha: str, file_path: str, file_sha256: str, key: str) -> dict:
    _check(bool(repo_root.strip()) and len(repo_root) <= 2000 and "\x00" not in repo_root, "CONSUMER_REPOSITORY_REQUIRED")
    _check(all(re.fullmatch(r"[0-9a-f]{40}", sha) for sha in [head_sha, base_sha]), "CONSUMER_REVISION_REQUIRED")
    _check(bool(re.fullmatch(r"[0-9a-f]{64}", file_sha256)), "CONSUMER_FILE_HASH_REQUIRED")
    path = Path(file_path)
    _check(bool(file_path.strip()) and "\x00" not in file_path and not path.is_absolute() and ".." not in path.parts and ".git" not in path.parts,
           "CONSUMER_FILE_PATH_INVALID")
    _check(0 < len(key.strip()) <= 200, "CONSUMER_IDEMPOTENCY_KEY_REQUIRED")
    return {"repo_root_raw": repo_root, "file_path_raw": file_path, "repo_root": str(Path(repo_root).expanduser().resolve()), "head_sha": head_sha, "base_sha": base_sha,
            "file_path": file_path, "file_sha256": file_sha256}


def prepare(cfg: Settings, *, binding_id: str, repo_root: str, head_sha: str, base_sha: str,
            file_path: str, file_sha256: str, idempotency_key: str) -> dict:
    args = _validate_inputs(repo_root, head_sha, base_sha, file_path, file_sha256, idempotency_key)
    with learning._store(cfg) as state:
        binding = _binding(state, binding_id)
        steps = []
        for name in RECIPES[binding["recipe_id"]]["tools"]:
            tool = TOOL_REGISTRY[name]
            _check(not tool.mutates_state and tool.network == "none" and tool.requires_approval, "CONSUMER_TOOL_AUTHORITY_DRIFT")
            arguments: dict[str, Any] = {"repo_root": args["repo_root"]}
            if name == "coding.diff":
                arguments.update(base=base_sha, max_bytes=500000)
            if name == "coding.read_file":
                arguments.update(path=file_path, max_bytes=200000)
            steps.append({"tool_name": name, "arguments": arguments, "tool_contract_sha256": learning.digest(tool.as_dict())})
        plan = {"schema": "mucaro-consumer-plan/1", "binding": dict(binding), "inputs": args, "steps": steps, "implementation_sha256": _implementation_sha256(), "root_identity": _root_identity(args["repo_root"])}
        plan_hash = learning.digest(plan)
        run_id = "consumer_" + learning.digest([binding["agent_id"], idempotency_key])
        runs = state.setdefault("consumer_runs", {})
        if run_id in runs:
            _check(runs[run_id]["plan_sha256"] == plan_hash, "CONSUMER_REPLAY_CONFLICT")
            return runs[run_id]
        row = {"schema": "mucaro-consumer-run/1", "run_id": run_id, "plan": plan, "plan_sha256": plan_hash, "state": "PREPARED", "version": 1,
               "created_at": utc_now(), "results": [], "error": None, "approval": None}
        runs[run_id] = row
        learning._receipt(state, "CONSUMER_RUN_PREPARED", binding["candidate_id"],
                          {"run_id": run_id, "plan_sha256": plan_hash})
        return row


def _implementation_sha256() -> str:
    root = Path(__file__).parent
    return learning.digest({str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                            for path in sorted(root.rglob("*.py"))})


def _run(state: dict, run_id: str) -> dict:
    row = state.get("consumer_runs", {}).get(run_id)
    _check(row is not None, "CONSUMER_RUN_NOT_FOUND")
    _check(learning.digest(row["plan"]) == row["plan_sha256"], "CONSUMER_PLAN_CORRUPT")
    return row


def _root_identity(path: str) -> dict:
    root = Path(path)
    _check(str(root.resolve()) == path, "CONSUMER_ROOT_CHANGED")
    try:
        stat = root.stat()
    except OSError as exc:
        raise learning.LearningError("CONSUMER_ROOT_UNAVAILABLE") from exc
    _check(root.is_dir(), "CONSUMER_ROOT_NOT_DIRECTORY")
    return {"canonical_root": path, "device": stat.st_dev, "inode": stat.st_ino}


def execute(cfg: Settings, run_id: str, *, version: int, plan_sha256: str, approved: bool) -> dict:
    _check(approved is True, "CONSUMER_RUN_APPROVAL_REQUIRED")
    with learning._store(cfg) as state:
        row = _run(state, run_id)
        _check(row["plan_sha256"] == plan_sha256, "CONSUMER_APPROVAL_STALE")
        if row["state"] in TERMINAL:
            return row
        _check(type(version) is int and row["version"] == version and row["state"] == "PREPARED", "CONSUMER_RUN_STALE")
        _binding(state, row["plan"]["binding"]["binding_id"])
        _check(row["plan"]["implementation_sha256"] == _implementation_sha256(), "CONSUMER_IMPLEMENTATION_STALE")
        row.update(state="RUNNING", version=version + 1, approval={"actor": "local_operator", "plan_sha256": plan_sha256})
        learning._receipt(state, "CONSUMER_RUN_APPROVED", row["plan"]["binding"]["candidate_id"],
                          {"run_id": run_id, "plan_sha256": plan_sha256})
    try:
        for index in range(len(row["plan"]["steps"])):
            # Serialize a step against revocation, persist the outcome with its learning receipt.
            # Tool receipts themselves persist independently; recovery never re-executes an unknown step.
            with learning._store(cfg) as state:
                row = _run(state, run_id)
                _check(row["state"] == "RUNNING", "CONSUMER_RUN_NOT_RUNNING")
                binding = _binding(state, row["plan"]["binding"]["binding_id"])
                _check(row["plan"]["implementation_sha256"] == _implementation_sha256(), "CONSUMER_IMPLEMENTATION_STALE")
                step = row["plan"]["steps"][index]
                definition = TOOL_REGISTRY[step["tool_name"]]
                _check(learning.digest(definition.as_dict()) == step["tool_contract_sha256"]
                       and not definition.mutates_state and definition.network == "none", "CONSUMER_TOOL_AUTHORITY_DRIFT")
                identity = _root_identity(row["plan"]["inputs"]["repo_root"])
                _check(identity == row["plan"]["root_identity"], "CONSUMER_ROOT_CHANGED")
                _check(not row.get("root_identity") or row["root_identity"] == identity, "CONSUMER_ROOT_CHANGED")
                row["root_identity"] = identity
                call = request_tool_call(step["tool_name"], step["arguments"], cfg=cfg, requested_by=binding["agent_id"],
                                         reason="Explicitly approved learned workflow plan", run_id=run_id)["tool_call"]
                approve_tool_call(call["tool_call_id"], cfg=cfg, approved_by="local_operator",
                                  expected_arguments_sha256=call["arguments_sha256"])
                outcome = run_tool_call(call["tool_call_id"], cfg=cfg, executed_by=binding["agent_id"])["result"]
                _check(outcome.get("head_sha") == row["plan"]["inputs"]["head_sha"], "CONSUMER_HEAD_CHANGED")
                _check(outcome.get("repo_root") == identity["canonical_root"], "CONSUMER_ROOT_CHANGED")
                _check(outcome.get("dirty_status", "CLEAN") == "CLEAN", "CONSUMER_DIRTY_SOURCE")
                if step["tool_name"] == "coding.diff":
                    _check(outcome["base_commit_sha"] == row["plan"]["inputs"]["base_sha"], "CONSUMER_BASE_CHANGED")
                if step["tool_name"] == "coding.read_file":
                    _check(outcome["sha256"] == row["plan"]["inputs"]["file_sha256"], "CONSUMER_FILE_CHANGED")
                _check(_root_identity(identity["canonical_root"]) == identity, "CONSUMER_ROOT_CHANGED")
                row["results"].append({"step": index, "tool_call_id": call["tool_call_id"], "tool": step["tool_name"],
                                       "result": outcome, "result_sha256": learning.digest(outcome)})
                row["version"] += 1
                learning._receipt(state, "CONSUMER_STEP_COMPLETED", binding["candidate_id"],
                                  {"run_id": run_id, "step": index, "tool_call_id": call["tool_call_id"],
                                   "result_sha256": learning.digest(outcome)})
        with learning._store(cfg) as state:
            row = _run(state, run_id)
            _check(row["state"] == "RUNNING", "CONSUMER_RUN_NOT_RUNNING")
            _binding(state, row["plan"]["binding"]["binding_id"])
            row.update(state="COMPLETED", version=row["version"] + 1, output_sha256=learning.digest(row["results"]))
            learning._receipt(state, "CONSUMER_RUN_COMPLETED", row["plan"]["binding"]["candidate_id"],
                              {"run_id": run_id, "output_sha256": row["output_sha256"]})
            return row
    except Exception as exc:  # noqa: BLE001 - retain an explicit FAILED receipt, never retry an unknown step
        with learning._store(cfg) as state:
            row = _run(state, run_id)
            if row["state"] == "RUNNING":
                row["interrupted_tool_calls"] = _cancel_unstarted(cfg, run_id, "Consumer run failed")
                row.update(state="FAILED", version=row["version"] + 1,
                           error={"type": type(exc).__name__, "detail": str(exc)})
                learning._receipt(state, "CONSUMER_RUN_FAILED", row["plan"]["binding"]["candidate_id"],
                                  {"run_id": run_id, "error": row["error"]})
            return row


def _cancel_unstarted(cfg: Settings, run_id: str, reason: str) -> list[dict]:
    calls = [c for c in list_tool_calls(cfg)["tool_calls"] if c.get("run_id") == run_id]
    observations = []
    for call in calls:
        observation = {"tool_call_id": call["tool_call_id"], "status": call["status"]}
        if call["status"] in {"PENDING_APPROVAL", "APPROVED"}:
            try:
                cancelled = cancel_tool_call(call["tool_call_id"], cfg=cfg, cancelled_by="local_operator", reason=reason)
                observation["status"] = cancelled["tool_call"]["status"]
            except ToolRuntimeError as exc:
                observation["cancel_error"] = exc.status
        observations.append(observation)
    return observations


def stop(cfg: Settings, run_id: str, *, version: int, reason: str) -> dict:
    _check(bool(reason.strip()), "CONSUMER_STOP_REASON_REQUIRED")
    with learning._store(cfg) as state:
        row = _run(state, run_id)
        _check(type(version) is int and row["version"] == version and row["state"] not in TERMINAL, "CONSUMER_RUN_STALE")
        terminal = "INTERRUPTED" if row["state"] == "RUNNING" else "CANCELLED"
        row.update(state=terminal, version=version + 1, error={"detail": reason})
        row["interrupted_tool_calls"] = _cancel_unstarted(cfg, run_id, reason)
        learning._receipt(state, "CONSUMER_RUN_" + terminal, row["plan"]["binding"]["candidate_id"],
                          {"run_id": run_id, "reason": reason, "tool_calls": row["interrupted_tool_calls"]})
        return row


def reward(cfg: Settings, run_id: str, *, output_sha256: str, score: float, criteria: str,
           permitted_use: bool) -> dict:
    _check(permitted_use is True, "FEEDBACK_PERMITTED_USE_REQUIRED")
    _check(type(score) in {float, int} and math.isfinite(score) and -1 <= score <= 1, "REWARD_RANGE")
    _check(bool(criteria.strip()) and len(criteria) <= 5000, "CONSUMER_REWARD_CRITERIA_REQUIRED")
    with learning._store(cfg) as state:
        row = _run(state, run_id)
        _check(row["state"] == "COMPLETED" and row["output_sha256"] == output_sha256, "CONSUMER_OUTPUT_STALE")
        _check(learning.digest(row["results"]) == output_sha256, "CONSUMER_OUTPUT_CORRUPT")
        binding = _binding(state, row["plan"]["binding"]["binding_id"])
        evidence = {"run_id": run_id, "output_sha256": output_sha256, "plan_sha256": row["plan_sha256"],
                    "criteria": criteria, "source": "operator_scored_actual_consumer_run"}
        return learning._record_feedback(state, episode_id="consumer-run:" + run_id, candidate_id=binding["candidate_id"],
                                 context=binding["scope"], reward=score, evidence=json.dumps(evidence, sort_keys=True),
                                 permitted_use=True)
