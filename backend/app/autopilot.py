from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema

from .agent_checkpoints import (
    CheckpointError,
    create_checkpoint,
    get_checkpoint,
    get_task_dag_state,
    install_task_dag_state,
    set_checkpoint_metadata_namespace,
    transition_checkpoint,
    transition_task_dag_state,
)
from .agent_task_dag import TaskDagError, build_task_dag
from .config import Settings, settings
from .tools import (
    TOOL_REGISTRY,
    ToolRuntimeError,
    approve_tool_call,
    arguments_digest,
    list_tool_calls,
    request_tool_call,
    run_tool_call,
)
from .utils import canonical_sha256, utc_now

AUTOPILOT_SCHEMA_VERSION = "mucaro-autopilot/1.0"
AUTOPILOT_NAMESPACE = "autopilot"
DEFAULT_BUDGETS: dict[str, int | None] = {
    "max_steps": 64,
    "max_tool_calls": 64,
    "max_mutations": 16,
    "max_remote_calls": 16,
    "max_replans": 0,
    "max_wall_seconds": 3600,
    "max_model_tokens": None,
    "max_cost_microunits": None,
    "max_no_progress": 2,
    "max_fingerprint_occurrences": 3,
}
MAX_HISTORY = 32


class AutopilotError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "schemas" / "autopilot_state.schema.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AutopilotError("ERROR_AUTOPILOT_SCHEMA_UNAVAILABLE", f"{path}: {exc}") from exc


def _validate_state(state: dict[str, Any]) -> dict[str, Any]:
    errors = sorted(jsonschema.Draft202012Validator(_schema()).iter_errors(state), key=lambda error: list(error.absolute_path))
    if errors:
        where = "/".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise AutopilotError("ERROR_AUTOPILOT_SCHEMA", f"{where}: {errors[0].message}")
    return state


def _normalize_budgets(overrides: dict[str, Any] | None) -> dict[str, int | None]:
    budgets = dict(DEFAULT_BUDGETS)
    if overrides is None:
        return budgets
    if not isinstance(overrides, dict):
        raise AutopilotError("ERROR_AUTOPILOT_BUDGETS", "budgets must be an object")
    unknown = sorted(set(overrides) - set(DEFAULT_BUDGETS))
    if unknown:
        raise AutopilotError("ERROR_AUTOPILOT_BUDGET_UNKNOWN", ",".join(unknown))
    budgets.update(overrides)
    probe = build_autopilot_state(budgets=budgets, _normalized=True)
    return dict(probe["budgets"])


def build_autopilot_state(
    *,
    budgets: dict[str, Any] | None = None,
    _normalized: bool = False,
) -> dict[str, Any]:
    if _normalized:
        normalized = dict(budgets or DEFAULT_BUDGETS)
    else:
        normalized = dict(DEFAULT_BUDGETS)
        if budgets:
            unknown = sorted(set(budgets) - set(DEFAULT_BUDGETS))
            if unknown:
                raise AutopilotError("ERROR_AUTOPILOT_BUDGET_UNKNOWN", ",".join(unknown))
            normalized.update(budgets)
    now = utc_now()
    state = {
        "schema_version": AUTOPILOT_SCHEMA_VERSION,
        "status": "PLANNED",
        "authorization_state": "PENDING",
        "authorized_by": None,
        "authorized_at": None,
        "started_at": None,
        "updated_at": now,
        "budgets": normalized,
        "counters": {
            "steps": 0,
            "tool_calls": 0,
            "mutations": 0,
            "remote_calls": 0,
            "replans": 0,
            "model_tokens": None,
            "cost_microunits": None,
        },
        "fingerprint_history": [],
        "no_progress_streak": 0,
        "stop_reason": None,
        "last_task_id": None,
    }
    return _validate_state(state)


def plan_autopilot(
    *,
    session_id: str,
    run_id: str,
    objective: str,
    source_sha: str,
    task_definitions: list[dict[str, Any]],
    budgets: dict[str, Any] | None = None,
    provider: str | None = None,
    model: str | None = None,
    cfg: Settings = settings,
) -> dict[str, Any]:
    state = build_autopilot_state(budgets=budgets)
    try:
        dag = build_task_dag(task_definitions)
    except TaskDagError as exc:
        raise AutopilotError(exc.status, exc.detail) from exc
    checkpoint = create_checkpoint(
        session_id=session_id,
        run_id=run_id,
        objective=objective,
        source_sha=source_sha,
        state="PLANNING",
        provider=provider,
        model=model,
        metadata={AUTOPILOT_NAMESPACE: state},
        cfg=cfg,
    )
    try:
        checkpoint = install_task_dag_state(
            checkpoint["checkpoint_id"],
            dag=dag,
            expected_sequence=checkpoint["sequence"],
            cfg=cfg,
        )
    except CheckpointError as exc:
        raise AutopilotError(exc.status, exc.detail) from exc
    return {
        "status": "AUTOPILOT_PLANNED",
        "checkpoint": checkpoint,
        "task_dag": get_task_dag_state(checkpoint["checkpoint_id"], cfg),
    }


def _state_from_checkpoint(checkpoint: dict[str, Any]) -> dict[str, Any]:
    raw = (checkpoint.get("metadata") or {}).get(AUTOPILOT_NAMESPACE)
    if not isinstance(raw, dict):
        raise AutopilotError("ERROR_AUTOPILOT_STATE_MISSING", checkpoint["checkpoint_id"])
    return _validate_state(deepcopy(raw))


def _set_state(
    checkpoint: dict[str, Any],
    state: dict[str, Any],
    *,
    cfg: Settings,
) -> dict[str, Any]:
    state = deepcopy(state)
    state["updated_at"] = utc_now()
    _validate_state(state)
    try:
        return set_checkpoint_metadata_namespace(
            checkpoint["checkpoint_id"],
            namespace=AUTOPILOT_NAMESPACE,
            value=state,
            expected_sequence=checkpoint["sequence"],
            cfg=cfg,
        )
    except CheckpointError as exc:
        raise AutopilotError(exc.status, exc.detail) from exc


def authorize_autopilot(
    checkpoint_id: str,
    *,
    approved_by: str,
    expected_sequence: int,
    cfg: Settings = settings,
) -> dict[str, Any]:
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise AutopilotError("ERROR_AUTOPILOT_AUTHORITY", "approved_by is required")
    checkpoint = get_checkpoint(checkpoint_id, cfg)
    if checkpoint is None:
        raise AutopilotError("ERROR_CHECKPOINT_NOT_FOUND", checkpoint_id)
    if checkpoint["sequence"] != expected_sequence:
        raise AutopilotError("ERROR_CHECKPOINT_CONFLICT", f"expected sequence {expected_sequence}, found {checkpoint['sequence']}")
    state = _state_from_checkpoint(checkpoint)

    if state["authorization_state"] != "AUTHORIZED":
        now = utc_now()
        state["authorization_state"] = "AUTHORIZED"
        state["authorized_by"] = approved_by.strip()
        state["authorized_at"] = now
        state["started_at"] = state["started_at"] or now
        state["status"] = "AUTHORIZED"
        state["stop_reason"] = None
        checkpoint = _set_state(checkpoint, state, cfg=cfg)

    if checkpoint["state"] in {"PLANNING", "READY_FOR_APPROVAL", "BLOCKED_PERMISSION", "BLOCKED_EXTERNAL", "PAUSED_PROVIDER", "PAUSED_RESOURCE"}:
        try:
            checkpoint = transition_checkpoint(
                checkpoint_id,
                state="EXECUTING",
                expected_sequence=checkpoint["sequence"],
                cfg=cfg,
            )
        except CheckpointError as exc:
            raise AutopilotError(exc.status, exc.detail) from exc
    elif checkpoint["state"] != "EXECUTING":
        raise AutopilotError("ERROR_AUTOPILOT_CHECKPOINT_STATE", checkpoint["state"])

    state = _state_from_checkpoint(checkpoint)
    state["status"] = "RUNNING"
    state["stop_reason"] = None
    checkpoint = _set_state(checkpoint, state, cfg=cfg)
    return {"status": "AUTOPILOT_AUTHORIZED", "checkpoint": checkpoint}


def update_autopilot_budgets(
    checkpoint_id: str,
    *,
    budgets: dict[str, Any],
    expected_sequence: int,
    cfg: Settings = settings,
) -> dict[str, Any]:
    checkpoint = get_checkpoint(checkpoint_id, cfg)
    if checkpoint is None:
        raise AutopilotError("ERROR_CHECKPOINT_NOT_FOUND", checkpoint_id)
    if checkpoint["sequence"] != expected_sequence:
        raise AutopilotError("ERROR_CHECKPOINT_CONFLICT", f"expected sequence {expected_sequence}, found {checkpoint['sequence']}")
    if checkpoint["state"] not in {"PLANNING", "PAUSED_RESOURCE", "BLOCKED_EXTERNAL"}:
        raise AutopilotError("ERROR_AUTOPILOT_BUDGET_STATE", "budgets change only while planned or paused")
    state = _state_from_checkpoint(checkpoint)
    merged_budgets = dict(state["budgets"])
    merged_budgets.update(budgets)
    state["budgets"] = _normalize_budgets(merged_budgets)
    state["stop_reason"] = None
    checkpoint = _set_state(checkpoint, state, cfg=cfg)
    return {"status": "AUTOPILOT_BUDGETS_UPDATED", "checkpoint": checkpoint}


def _autopilot_calls(checkpoint: dict[str, Any], cfg: Settings) -> list[dict[str, Any]]:
    prefix = f"autopilot:{checkpoint['checkpoint_id']}:"
    calls = list_tool_calls(cfg)["tool_calls"]
    return [
        call
        for call in calls
        if call.get("run_id") == checkpoint["run_id"]
        and call.get("requested_by") == "autopilot"
        and isinstance(call.get("reason"), str)
        and call["reason"].startswith(prefix)
    ]


def _usage(checkpoint: dict[str, Any], dag: dict[str, Any], cfg: Settings) -> dict[str, int | None]:
    calls = _autopilot_calls(checkpoint, cfg)
    steps = sum(int(task.get("attempt_count", 0)) for task in dag["tasks"])
    mutations = sum(
        1
        for call in calls
        if call.get("tool_name") in TOOL_REGISTRY and TOOL_REGISTRY[call["tool_name"]].mutates_state
    )
    remote = sum(
        1
        for call in calls
        if call.get("tool_name") in TOOL_REGISTRY and TOOL_REGISTRY[call["tool_name"]].network != "none"
    )
    state = _state_from_checkpoint(checkpoint)
    return {
        "steps": steps,
        "tool_calls": len(calls),
        "mutations": mutations,
        "remote_calls": remote,
        "replans": int(state["counters"].get("replans") or 0),
        "model_tokens": None,
        "cost_microunits": None,
    }


def _elapsed_seconds(state: dict[str, Any]) -> int:
    started = state.get("started_at")
    if not isinstance(started, str):
        return 0
    try:
        then = datetime.fromisoformat(started.replace("Z", "+00:00"))
    except ValueError:
        raise AutopilotError("ERROR_AUTOPILOT_TIME", "started_at is not ISO-8601")
    return max(0, int((datetime.now(UTC) - then).total_seconds()))


def _global_budget_reason(state: dict[str, Any]) -> str | None:
    budgets = state["budgets"]
    if budgets["max_model_tokens"] is not None and state["counters"]["model_tokens"] is None:
        return "BUDGET_UNVERIFIABLE_MODEL_TOKENS"
    if budgets["max_cost_microunits"] is not None and state["counters"]["cost_microunits"] is None:
        return "BUDGET_UNVERIFIABLE_COST"
    if budgets["max_wall_seconds"] is not None and _elapsed_seconds(state) >= budgets["max_wall_seconds"]:
        return "BUDGET_EXHAUSTED_WALL_TIME"
    if budgets["max_replans"] is not None and state["counters"]["replans"] > budgets["max_replans"]:
        return "BUDGET_EXHAUSTED_REPLANS"
    return None


def _new_action_budget_reason(
    state: dict[str, Any],
    usage: dict[str, int | None],
    *,
    tool_name: str,
) -> str | None:
    budgets = state["budgets"]
    tool = TOOL_REGISTRY[tool_name]
    if budgets["max_steps"] is not None and int(usage["steps"] or 0) >= budgets["max_steps"]:
        return "BUDGET_EXHAUSTED_STEPS"
    if budgets["max_tool_calls"] is not None and int(usage["tool_calls"] or 0) >= budgets["max_tool_calls"]:
        return "BUDGET_EXHAUSTED_TOOL_CALLS"
    if tool.mutates_state and budgets["max_mutations"] is not None and int(usage["mutations"] or 0) >= budgets["max_mutations"]:
        return "BUDGET_EXHAUSTED_MUTATIONS"
    if tool.network != "none" and budgets["max_remote_calls"] is not None and int(usage["remote_calls"] or 0) >= budgets["max_remote_calls"]:
        return "BUDGET_EXHAUSTED_REMOTE_CALLS"
    return None


def _progress_fingerprint(checkpoint: dict[str, Any], dag: dict[str, Any], cfg: Settings) -> str:
    calls = _autopilot_calls(checkpoint, cfg)
    return canonical_sha256(
        {
            "source_sha": checkpoint["source_sha"],
            "dag_definition_sha256": dag["definition_sha256"],
            "tasks": [
                {
                    "id": task["id"],
                    "state": task["state"],
                    "attempt_count": task["attempt_count"],
                    "evidence": task["evidence"],
                    "artifacts": task["artifacts"],
                    "blocked_reason": task["blocked_reason"],
                }
                for task in dag["tasks"]
            ],
            "contradictions": [
                {"id": item.get("id"), "state": item.get("state"), "resolution": item.get("resolution")}
                for item in checkpoint.get("contradictions", [])
            ],
            "tool_calls": [
                {
                    "tool_call_id": call.get("tool_call_id"),
                    "tool_name": call.get("tool_name"),
                    "arguments_sha256": call.get("arguments_sha256"),
                    "status": call.get("status"),
                    "result_status": (call.get("result") or {}).get("status") if isinstance(call.get("result"), dict) else None,
                }
                for call in calls
            ],
        }
    )


def classify_progress(
    *,
    history: list[str],
    no_progress_streak: int,
    before: str,
    after: str,
    max_no_progress: int,
    max_fingerprint_occurrences: int,
) -> tuple[list[str], int, str | None]:
    """Classify repeated state without treating deterministic repetition as progress evidence."""
    if not all(isinstance(value, str) and len(value) == 64 for value in [*history, before, after]):
        raise AutopilotError("ERROR_AUTOPILOT_FINGERPRINT", "progress fingerprints must be 64-character hashes")
    if max_no_progress < 1 or max_fingerprint_occurrences < 2:
        raise AutopilotError("ERROR_AUTOPILOT_PROGRESS_POLICY", "progress thresholds are invalid")
    streak = no_progress_streak + 1 if before == after else 0
    updated = [*history, after][-MAX_HISTORY:]
    if streak >= max_no_progress:
        return updated, streak, "NO_PROGRESS"
    if updated.count(after) >= max_fingerprint_occurrences:
        return updated, streak, "LOOP_DETECTED"
    return updated, streak, None


def _record_progress(
    checkpoint: dict[str, Any],
    *,
    before: str,
    after: str,
    last_task_id: str | None,
    cfg: Settings,
) -> tuple[dict[str, Any], str | None]:
    state = _state_from_checkpoint(checkpoint)
    history, streak, reason = classify_progress(
        history=list(state["fingerprint_history"]),
        no_progress_streak=int(state["no_progress_streak"]),
        before=before,
        after=after,
        max_no_progress=int(state["budgets"]["max_no_progress"]),
        max_fingerprint_occurrences=int(state["budgets"]["max_fingerprint_occurrences"]),
    )
    state["fingerprint_history"] = history
    state["no_progress_streak"] = streak
    state["last_task_id"] = last_task_id
    state["counters"] = _usage(checkpoint, get_task_dag_state(checkpoint["checkpoint_id"], cfg), cfg)

    checkpoint = _set_state(checkpoint, state, cfg=cfg)
    return checkpoint, reason


def _set_runtime_status(
    checkpoint: dict[str, Any],
    *,
    status: str,
    stop_reason: str | None,
    cfg: Settings,
) -> dict[str, Any]:
    state = _state_from_checkpoint(checkpoint)
    state["status"] = status
    state["stop_reason"] = stop_reason
    state["counters"] = _usage(checkpoint, get_task_dag_state(checkpoint["checkpoint_id"], cfg), cfg)
    return _set_state(checkpoint, state, cfg=cfg)


def _action(task: dict[str, Any]) -> dict[str, Any] | None:
    raw = (task.get("metadata") or {}).get("action")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise AutopilotError("ERROR_AUTOPILOT_ACTION", f"{task['id']}: action must be an object")
    allowed = {"tool_name", "arguments", "success_statuses"}
    if set(raw) - allowed:
        raise AutopilotError("ERROR_AUTOPILOT_ACTION", f"{task['id']}: action has unknown fields")
    tool_name = raw.get("tool_name")
    arguments = raw.get("arguments")
    success_statuses = raw.get("success_statuses")
    if not isinstance(tool_name, str) or not tool_name:
        raise AutopilotError("ERROR_AUTOPILOT_ACTION", f"{task['id']}: tool_name is required")
    if not isinstance(arguments, dict):
        raise AutopilotError("ERROR_AUTOPILOT_ACTION", f"{task['id']}: arguments must be an object")
    if not isinstance(success_statuses, list) or not success_statuses or not all(isinstance(item, str) and item for item in success_statuses):
        raise AutopilotError("ERROR_AUTOPILOT_ACTION", f"{task['id']}: non-empty success_statuses is required")
    return {
        "tool_name": tool_name,
        "arguments": deepcopy(arguments),
        "success_statuses": list(dict.fromkeys(success_statuses)),
    }


def _task_call(
    checkpoint: dict[str, Any],
    task: dict[str, Any],
    action: dict[str, Any],
    cfg: Settings,
) -> dict[str, Any] | None:
    digest = arguments_digest(action["arguments"])
    action_sha = canonical_sha256(action)
    reason = f"autopilot:{checkpoint['checkpoint_id']}:{task['id']}:{action_sha}"
    artifact_rows = [
        artifact
        for artifact in task.get("artifacts", [])
        if isinstance(artifact, dict) and artifact.get("kind") == "tool_call" and artifact.get("tool_call_id")
    ]
    artifact_ids = list(dict.fromkeys(artifact["tool_call_id"] for artifact in artifact_rows))
    calls = list_tool_calls(cfg)["tool_calls"]
    by_id = {call["tool_call_id"]: call for call in calls}

    def validate_identity(call: dict[str, Any]) -> dict[str, Any]:
        if (
            call.get("run_id") != checkpoint["run_id"]
            or call.get("requested_by") != "autopilot"
            or call.get("tool_name") != action["tool_name"]
            or call.get("arguments_sha256") != digest
            or call.get("reason") != reason
        ):
            raise AutopilotError("ERROR_AUTOPILOT_TOOL_CALL_IDENTITY", task["id"])
        return call

    if len(artifact_ids) > 1:
        raise AutopilotError("ERROR_AUTOPILOT_AMBIGUOUS_TOOL_CALL", task["id"])
    if artifact_ids:
        call = by_id.get(artifact_ids[0])
        if call is None:
            raise AutopilotError("ERROR_AUTOPILOT_TOOL_CALL_MISSING", artifact_ids[0])
        call = validate_identity(call)
        artifact = artifact_rows[0]
        if (
            artifact.get("tool_name") != call.get("tool_name")
            or artifact.get("arguments_sha256") != call.get("arguments_sha256")
        ):
            raise AutopilotError("ERROR_AUTOPILOT_TOOL_ARTIFACT_IDENTITY", task["id"])
        return call

    candidates = [
        validate_identity(call)
        for call in calls
        if call.get("run_id") == checkpoint["run_id"]
        and call.get("requested_by") == "autopilot"
        and call.get("tool_name") == action["tool_name"]
        and call.get("arguments_sha256") == digest
        and call.get("reason") == reason
    ]
    if len(candidates) > 1:
        raise AutopilotError("ERROR_AUTOPILOT_AMBIGUOUS_TOOL_CALL", task["id"])
    return candidates[0] if candidates else None


def _tool_artifact(call: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "tool_call",
        "tool_call_id": call["tool_call_id"],
        "tool_name": call["tool_name"],
        "arguments_sha256": call["arguments_sha256"],
    }


def _transition_task(
    checkpoint: dict[str, Any],
    *,
    task_id: str,
    state: str,
    cfg: Settings,
    evidence: list[dict[str, Any]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    blocked_reason: str | None = None,
) -> dict[str, Any]:
    try:
        return transition_task_dag_state(
            checkpoint["checkpoint_id"],
            task_id=task_id,
            state=state,
            expected_sequence=checkpoint["sequence"],
            evidence=evidence,
            artifacts=artifacts,
            blocked_reason=blocked_reason,
            cfg=cfg,
        )
    except CheckpointError as exc:
        raise AutopilotError(exc.status, exc.detail) from exc


def _terminalize_tool_result(
    checkpoint: dict[str, Any],
    *,
    task: dict[str, Any],
    action: dict[str, Any],
    call: dict[str, Any],
    cfg: Settings,
) -> tuple[dict[str, Any], str]:
    if task["state"] != "RUNNING":
        checkpoint = _transition_task(
            checkpoint,
            task_id=task["id"],
            state="RUNNING",
            artifacts=[] if any(
                artifact.get("tool_call_id") == call["tool_call_id"]
                for artifact in task.get("artifacts", [])
                if isinstance(artifact, dict)
            ) else [_tool_artifact(call)],
            cfg=cfg,
        )
    result = call.get("result") if isinstance(call.get("result"), dict) else {}
    evidence = [{
        "kind": "tool_result",
        "tool_call_id": call["tool_call_id"],
        "tool_call_status": call.get("status"),
        "result_status": result.get("status"),
        "result_sha256": canonical_sha256(result),
    }]
    if call.get("status") == "EXECUTED" and result.get("status") in action["success_statuses"]:
        checkpoint = _transition_task(checkpoint, task_id=task["id"], state="PASSED", evidence=evidence, cfg=cfg)
        return checkpoint, "PASSED"
    checkpoint = _transition_task(checkpoint, task_id=task["id"], state="FAILED", evidence=evidence, cfg=cfg)
    return checkpoint, "FAILED"


def _pause_for_budget(checkpoint: dict[str, Any], reason: str, cfg: Settings) -> dict[str, Any]:
    checkpoint = _set_runtime_status(checkpoint, status="PAUSED", stop_reason=reason, cfg=cfg)
    try:
        return transition_checkpoint(
            checkpoint["checkpoint_id"],
            state="PAUSED_RESOURCE",
            pause_reason=reason,
            expected_sequence=checkpoint["sequence"],
            cfg=cfg,
        )
    except CheckpointError as exc:
        raise AutopilotError(exc.status, exc.detail) from exc


def advance_autopilot(checkpoint_id: str, *, cfg: Settings = settings) -> dict[str, Any]:
    checkpoint = get_checkpoint(checkpoint_id, cfg)
    if checkpoint is None:
        raise AutopilotError("ERROR_CHECKPOINT_NOT_FOUND", checkpoint_id)
    state = _state_from_checkpoint(checkpoint)
    if state["authorization_state"] != "AUTHORIZED":
        return {"status": "AUTOPILOT_NOT_AUTHORIZED", "checkpoint": checkpoint}
    if checkpoint["state"] != "EXECUTING":
        return {
            "status": "AUTOPILOT_PAUSED",
            "reason": checkpoint.get("pause_reason") or checkpoint["state"],
            "checkpoint": checkpoint,
        }

    dag = get_task_dag_state(checkpoint_id, cfg)
    state["counters"] = _usage(checkpoint, dag, cfg)
    global_budget = _global_budget_reason(state)
    if global_budget:
        checkpoint = _pause_for_budget(checkpoint, global_budget, cfg)
        return {"status": "AUTOPILOT_STOPPED", "reason": global_budget, "checkpoint": checkpoint}

    by_id = {task["id"]: task for task in dag["tasks"]}
    active = [task for task in dag["tasks"] if task["state"] in {"RUNNING", "AWAITING_APPROVAL", "AWAITING_INPUT"}]
    ready = [by_id[task_id] for task_id in dag["ready_task_ids"]]

    if not active and not ready:
        if all(task["state"] == "PASSED" for task in dag["tasks"]):
            checkpoint = _set_runtime_status(checkpoint, status="COMPLETED", stop_reason="OBJECTIVE_SATISFIED", cfg=cfg)
            try:
                checkpoint = transition_checkpoint(
                    checkpoint_id,
                    state="COMPLETED",
                    expected_sequence=checkpoint["sequence"],
                    cfg=cfg,
                )
            except CheckpointError as exc:
                raise AutopilotError(exc.status, exc.detail) from exc
            return {"status": "AUTOPILOT_COMPLETED", "reason": "OBJECTIVE_SATISFIED", "checkpoint": checkpoint}
        residue = {task["id"]: task["state"] for task in dag["tasks"] if task["state"] != "PASSED"}
        checkpoint = _set_runtime_status(checkpoint, status="STOPPED", stop_reason="VECTOR_EXHAUSTED", cfg=cfg)
        try:
            checkpoint = transition_checkpoint(
                checkpoint_id,
                state="FAILED",
                expected_sequence=checkpoint["sequence"],
                cfg=cfg,
            )
        except CheckpointError as exc:
            raise AutopilotError(exc.status, exc.detail) from exc
        return {"status": "AUTOPILOT_STOPPED", "reason": "VECTOR_EXHAUSTED", "residue": residue, "checkpoint": checkpoint}

    task = active[0] if active else ready[0]
    if task["state"] == "AWAITING_INPUT":
        checkpoint = _set_runtime_status(checkpoint, status="WAITING_INPUT", stop_reason=None, cfg=cfg)
        return {"status": "AUTOPILOT_WAITING_INPUT", "task_id": task["id"], "checkpoint": checkpoint}

    before = _progress_fingerprint(checkpoint, dag, cfg)
    try:
        action = _action(task)
    except AutopilotError as exc:
        checkpoint = _transition_task(
            checkpoint,
            task_id=task["id"],
            state="BLOCKED",
            blocked_reason=exc.status,
            cfg=cfg,
        )
        checkpoint = _set_runtime_status(checkpoint, status="STOPPED", stop_reason="REQUIRED_CAPABILITY_UNAVAILABLE", cfg=cfg)
        return {"status": "AUTOPILOT_STOPPED", "reason": "REQUIRED_CAPABILITY_UNAVAILABLE", "task_id": task["id"], "checkpoint": checkpoint}

    if action is None:
        if task["state"] == "READY":
            checkpoint = _transition_task(checkpoint, task_id=task["id"], state="AWAITING_INPUT", cfg=cfg)
        checkpoint = _set_runtime_status(checkpoint, status="WAITING_INPUT", stop_reason=None, cfg=cfg)
        return {"status": "AUTOPILOT_WAITING_INPUT", "task_id": task["id"], "checkpoint": checkpoint}

    if action["tool_name"] not in TOOL_REGISTRY:
        if task["state"] in {"READY", "AWAITING_APPROVAL", "RUNNING"}:
            checkpoint = _transition_task(
                checkpoint,
                task_id=task["id"],
                state="BLOCKED",
                blocked_reason=f"tool_unavailable:{action['tool_name']}",
                cfg=cfg,
            )
        checkpoint = _set_runtime_status(checkpoint, status="STOPPED", stop_reason="REQUIRED_CAPABILITY_UNAVAILABLE", cfg=cfg)
        return {"status": "AUTOPILOT_STOPPED", "reason": "REQUIRED_CAPABILITY_UNAVAILABLE", "task_id": task["id"], "checkpoint": checkpoint}

    call = _task_call(checkpoint, task, action, cfg)
    if call is None:
        usage = _usage(checkpoint, dag, cfg)
        reason = _new_action_budget_reason(state, usage, tool_name=action["tool_name"])
        if reason:
            checkpoint = _pause_for_budget(checkpoint, reason, cfg)
            return {"status": "AUTOPILOT_STOPPED", "reason": reason, "checkpoint": checkpoint}
        action_sha = canonical_sha256(action)
        requested = request_tool_call(
            action["tool_name"],
            action["arguments"],
            cfg=cfg,
            requested_by="autopilot",
            reason=f"autopilot:{checkpoint['checkpoint_id']}:{task['id']}:{action_sha}",
            run_id=checkpoint["run_id"],
        )
        call = requested["tool_call"]
        if task["state"] == "READY":
            checkpoint = _transition_task(
                checkpoint,
                task_id=task["id"],
                state="AWAITING_APPROVAL",
                artifacts=[_tool_artifact(call)],
                cfg=cfg,
            )
        dag = get_task_dag_state(checkpoint_id, cfg)
        task = {item["id"]: item for item in dag["tasks"]}[task["id"]]

    tool = TOOL_REGISTRY[action["tool_name"]]
    auto_approvable = not tool.mutates_state and tool.network == "none"
    if call["status"] == "PENDING_APPROVAL":
        if not auto_approvable:
            checkpoint = _set_runtime_status(checkpoint, status="WAITING_APPROVAL", stop_reason=None, cfg=cfg)
            return {
                "status": "AUTOPILOT_WAITING_APPROVAL",
                "task_id": task["id"],
                "tool_call_id": call["tool_call_id"],
                "checkpoint": checkpoint,
            }
        try:
            approved = approve_tool_call(
                call["tool_call_id"],
                cfg=cfg,
                approved_by="autopilot:local-readonly-policy",
                reason="authorized autopilot may auto-approve local non-mutating tools",
                expected_arguments_sha256=call["arguments_sha256"],
            )
        except ToolRuntimeError as exc:
            raise AutopilotError(exc.status, exc.detail) from exc
        call = approved["tool_call"]

    if call["status"] in {"DENIED", "CANCELLED", "INTERRUPTED"}:
        checkpoint = _transition_task(
            checkpoint,
            task_id=task["id"],
            state="BLOCKED",
            blocked_reason=f"tool_call_{call['status'].lower()}:{call['tool_call_id']}",
            cfg=cfg,
        )
        stop_reason = "REQUIRED_APPROVAL_DENIED" if call["status"] == "DENIED" else "TOOL_EXECUTION_UNRESOLVED"
        checkpoint = _set_runtime_status(checkpoint, status="STOPPED", stop_reason=stop_reason, cfg=cfg)
        return {"status": "AUTOPILOT_STOPPED", "reason": stop_reason, "task_id": task["id"], "checkpoint": checkpoint}

    if call["status"] == "APPROVED":
        if task["state"] != "RUNNING":
            checkpoint = _transition_task(
                checkpoint,
                task_id=task["id"],
                state="RUNNING",
                artifacts=[] if any(
                    artifact.get("tool_call_id") == call["tool_call_id"]
                    for artifact in task.get("artifacts", [])
                    if isinstance(artifact, dict)
                ) else [_tool_artifact(call)],
                cfg=cfg,
            )
        try:
            run_tool_call(call["tool_call_id"], cfg=cfg, executed_by="autopilot")
        except ToolRuntimeError:
            pass
        calls = {row["tool_call_id"]: row for row in list_tool_calls(cfg)["tool_calls"]}
        call = calls[call["tool_call_id"]]

    dag = get_task_dag_state(checkpoint_id, cfg)
    task = {item["id"]: item for item in dag["tasks"]}[task["id"]]
    if call["status"] == "RUNNING":
        if task["state"] != "RUNNING":
            checkpoint = _transition_task(checkpoint, task_id=task["id"], state="RUNNING", cfg=cfg)
        checkpoint = _set_runtime_status(checkpoint, status="RUNNING", stop_reason=None, cfg=cfg)
        return {"status": "AUTOPILOT_TOOL_IN_FLIGHT", "task_id": task["id"], "tool_call_id": call["tool_call_id"], "checkpoint": checkpoint}

    if call["status"] in {"EXECUTED", "FAILED"}:
        checkpoint, terminal_state = _terminalize_tool_result(
            checkpoint,
            task=task,
            action=action,
            call=call,
            cfg=cfg,
        )
        dag = get_task_dag_state(checkpoint_id, cfg)
        after = _progress_fingerprint(checkpoint, dag, cfg)
        checkpoint, progress_reason = _record_progress(
            checkpoint,
            before=before,
            after=after,
            last_task_id=task["id"],
            cfg=cfg,
        )
        if progress_reason:
            checkpoint = _pause_for_budget(checkpoint, progress_reason, cfg)
            return {"status": "AUTOPILOT_STOPPED", "reason": progress_reason, "task_id": task["id"], "checkpoint": checkpoint}
        return {
            "status": "AUTOPILOT_PROGRESS",
            "task_id": task["id"],
            "task_state": terminal_state,
            "tool_call_id": call["tool_call_id"],
            "checkpoint": checkpoint,
        }

    raise AutopilotError("ERROR_AUTOPILOT_TOOL_STATE", f"{call['tool_call_id']}:{call['status']}")


def run_autopilot(
    checkpoint_id: str,
    *,
    max_iterations: int = 64,
    cfg: Settings = settings,
) -> dict[str, Any]:
    if not isinstance(max_iterations, int) or isinstance(max_iterations, bool) or max_iterations < 1 or max_iterations > 1024:
        raise AutopilotError("ERROR_AUTOPILOT_ITERATION_LIMIT", "max_iterations must be 1..1024")
    last: dict[str, Any] | None = None
    for _ in range(max_iterations):
        last = advance_autopilot(checkpoint_id, cfg=cfg)
        if last["status"] != "AUTOPILOT_PROGRESS":
            return last
    checkpoint = get_checkpoint(checkpoint_id, cfg)
    return {
        "status": "AUTOPILOT_SLICE_EXHAUSTED",
        "reason": "ITERATION_SLICE_LIMIT",
        "checkpoint": checkpoint,
        "last": last,
    }
