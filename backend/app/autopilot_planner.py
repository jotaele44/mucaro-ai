from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from .agent_checkpoints import (
    CheckpointError,
    get_checkpoint,
    get_task_dag_state,
    set_checkpoint_metadata_namespace,
)
from .autopilot import AutopilotError, plan_autopilot
from .config import Settings, settings
from .model_router import ModelProviderError, generate_answer
from .tools import TOOL_REGISTRY
from .utils import canonical_sha256, utc_now

MODEL_PLAN_NAMESPACE = "model_plan"
REPLAN_NAMESPACE = "autopilot_replan"
MODEL_PLAN_SCHEMA_VERSION = "mucaro-autopilot-model-plan/1.0"
REPLAN_SCHEMA_VERSION = "mucaro-autopilot-replan/1.0"
MAX_MODEL_PLAN_CHARS = 200_000
_ALLOWED_TOP_LEVEL = {"tasks", "rationale"}
_ALLOWED_REPLAN_KINDS = {"NEW_EVIDENCE", "TASK_FAILURE", "EXTERNAL_CHANGE"}


class AutopilotPlannerError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _eligible_tools() -> list[dict[str, Any]]:
    rows = []
    for name, tool in sorted(TOOL_REGISTRY.items()):
        if not tool.autopilot_success_statuses:
            continue
        rows.append(
            {
                "name": name,
                "mutates_state": tool.mutates_state,
                "network": tool.network,
                "description": tool.description,
            }
        )
    return rows


def _extract_json_object(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise AutopilotPlannerError("ERROR_AUTOPILOT_MODEL_PLAN_EMPTY", "model returned no plan text")
    if len(text) > MAX_MODEL_PLAN_CHARS:
        raise AutopilotPlannerError("ERROR_AUTOPILOT_MODEL_PLAN_TOO_LARGE", "model plan response exceeds size ceiling")
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) < 3 or not lines[-1].strip().startswith("```"):
            raise AutopilotPlannerError("ERROR_AUTOPILOT_MODEL_PLAN_JSON", "unterminated fenced JSON response")
        stripped = "\n".join(lines[1:-1]).strip()
        if stripped.lower().startswith("json\n"):
            stripped = stripped[5:].lstrip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise AutopilotPlannerError("ERROR_AUTOPILOT_MODEL_PLAN_JSON", str(exc)) from exc
    if not isinstance(payload, dict):
        raise AutopilotPlannerError("ERROR_AUTOPILOT_MODEL_PLAN_SHAPE", "model plan must be one JSON object")
    unknown = sorted(set(payload) - _ALLOWED_TOP_LEVEL)
    if unknown:
        raise AutopilotPlannerError("ERROR_AUTOPILOT_MODEL_PLAN_FIELD", ",".join(unknown))
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise AutopilotPlannerError("ERROR_AUTOPILOT_MODEL_PLAN_TASKS", "model plan requires a non-empty tasks array")
    if len(tasks) > 64:
        raise AutopilotPlannerError("ERROR_AUTOPILOT_MODEL_PLAN_TASKS", "model plan exceeds 64-task generation ceiling")
    rationale = payload.get("rationale", "")
    if not isinstance(rationale, str) or len(rationale) > 8000:
        raise AutopilotPlannerError("ERROR_AUTOPILOT_MODEL_PLAN_RATIONALE", "rationale must be a bounded string")
    return {"tasks": tasks, "rationale": rationale}


def _planner_prompt(
    *,
    objective: str,
    source_sha: str,
    prior_dag: dict[str, Any] | None = None,
    trigger: dict[str, Any] | None = None,
) -> str:
    tools = _eligible_tools()
    contract: dict[str, Any] = {
        "objective": objective,
        "source_sha": source_sha,
        "allowed_tools": tools,
        "required_output": {
            "tasks": [
                {
                    "id": "stable-id",
                    "title": "short title",
                    "description": "bounded description",
                    "depends_on": [],
                    "metadata": {
                        "action": {
                            "tool_name": "exact allowed tool name",
                            "arguments": {},
                        }
                    },
                }
            ],
            "rationale": "brief rationale",
        },
        "rules": [
            "Return JSON only; no prose outside the JSON object.",
            "Use only allowed_tools and exact tool names.",
            "Do not invent tool results, repository identity, approvals, credentials, files, or evidence.",
            "Every action arguments value must be a JSON object.",
            "Keep the DAG acyclic and deterministic; dependencies must reference task ids in this plan.",
            "Prefer the smallest plan that can satisfy the objective.",
            "Mutation and remote steps remain approval/lease gated; planning does not grant authority.",
        ],
    }
    if prior_dag is not None:
        contract["prior_plan"] = {
            "definition_sha256": prior_dag.get("definition_sha256"),
            "tasks": [
                {
                    "id": task.get("id"),
                    "state": task.get("state"),
                    "title": task.get("title"),
                    "depends_on": task.get("depends_on"),
                    "action": (task.get("metadata") or {}).get("action"),
                }
                for task in prior_dag.get("tasks", [])
            ],
        }
    if trigger is not None:
        contract["replan_trigger"] = trigger
        contract["rules"].append(
            "Replan only to address the supplied new evidence/failure/change; do not rewrite unrelated completed intent."
        )
    return (
        "You are the bounded Mucaro Autopilot planner. Produce the requested canonical task-DAG proposal.\n"
        + json.dumps(contract, sort_keys=True, ensure_ascii=False)
    )


def _model_plan(
    *,
    objective: str,
    source_sha: str,
    provider: str | None,
    model: str | None,
    prior_dag: dict[str, Any] | None = None,
    trigger: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    prompt = _planner_prompt(
        objective=objective,
        source_sha=source_sha,
        prior_dag=prior_dag,
        trigger=trigger,
    )
    try:
        response = generate_answer(
            prompt,
            [],
            model_provider=provider,
            model_name=model,
            allow_mock_fallback=False,
        )
    except ModelProviderError as exc:
        raise AutopilotPlannerError(exc.status, exc.detail) from exc
    payload = _extract_json_object(str(response.get("answer") or ""))
    model_receipt = {
        "provider": response.get("model_provider"),
        "model": response.get("model_name"),
        "response_sha256": canonical_sha256(str(response.get("answer") or "")),
        "usage": deepcopy(response.get("model_usage")) if isinstance(response.get("model_usage"), dict) else None,
        "prompt_sha256": canonical_sha256(prompt),
    }
    return payload, model_receipt


def _attach_plan_receipt(
    checkpoint: dict[str, Any],
    *,
    payload: dict[str, Any],
    model_receipt: dict[str, Any],
    parent_checkpoint_id: str | None,
    trigger: dict[str, Any] | None,
    cfg: Settings,
) -> dict[str, Any]:
    receipt = {
        "schema_version": MODEL_PLAN_SCHEMA_VERSION,
        "created_at": utc_now(),
        "checkpoint_id": checkpoint["checkpoint_id"],
        "parent_checkpoint_id": parent_checkpoint_id,
        "objective_sha256": canonical_sha256(checkpoint["objective"]),
        "source_sha": checkpoint["source_sha"],
        "task_definitions_sha256": canonical_sha256(payload["tasks"]),
        "rationale_sha256": canonical_sha256(payload.get("rationale") or ""),
        "trigger_sha256": canonical_sha256(trigger) if trigger is not None else None,
        "model": model_receipt,
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    try:
        return set_checkpoint_metadata_namespace(
            checkpoint["checkpoint_id"],
            namespace=MODEL_PLAN_NAMESPACE,
            value=receipt,
            expected_sequence=checkpoint["sequence"],
            cfg=cfg,
        )
    except CheckpointError as exc:
        raise AutopilotPlannerError(exc.status, exc.detail) from exc


def generate_autopilot_plan(
    *,
    session_id: str,
    run_id: str,
    objective: str,
    source_sha: str,
    budgets: dict[str, Any] | None = None,
    provider: str | None = None,
    model: str | None = None,
    cfg: Settings = settings,
) -> dict[str, Any]:
    payload, model_receipt = _model_plan(
        objective=objective,
        source_sha=source_sha,
        provider=provider,
        model=model,
    )
    try:
        planned = plan_autopilot(
            session_id=session_id,
            run_id=run_id,
            objective=objective,
            source_sha=source_sha,
            task_definitions=payload["tasks"],
            budgets=budgets,
            provider=provider,
            model=model,
            cfg=cfg,
        )
    except AutopilotError as exc:
        raise AutopilotPlannerError(exc.status, exc.detail) from exc
    checkpoint = _attach_plan_receipt(
        planned["checkpoint"],
        payload=payload,
        model_receipt=model_receipt,
        parent_checkpoint_id=None,
        trigger=None,
        cfg=cfg,
    )
    return {
        "status": "AUTOPILOT_MODEL_PLAN_READY",
        "checkpoint": checkpoint,
        "task_dag": get_task_dag_state(checkpoint["checkpoint_id"], cfg),
        "rationale": payload.get("rationale") or "",
        "plan_receipt": checkpoint["metadata"][MODEL_PLAN_NAMESPACE],
    }


def _validate_replan_trigger(trigger: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(trigger, dict):
        raise AutopilotPlannerError("ERROR_AUTOPILOT_REPLAN_TRIGGER", "replan trigger must be an object")
    allowed = {"kind", "detail", "evidence"}
    unknown = sorted(set(trigger) - allowed)
    if unknown:
        raise AutopilotPlannerError("ERROR_AUTOPILOT_REPLAN_TRIGGER", f"unknown fields: {','.join(unknown)}")
    kind = trigger.get("kind")
    detail = trigger.get("detail")
    evidence = trigger.get("evidence", [])
    if kind not in _ALLOWED_REPLAN_KINDS:
        raise AutopilotPlannerError("ERROR_AUTOPILOT_REPLAN_TRIGGER", "kind must be NEW_EVIDENCE, TASK_FAILURE, or EXTERNAL_CHANGE")
    if not isinstance(detail, str) or not detail.strip() or len(detail) > 8000:
        raise AutopilotPlannerError("ERROR_AUTOPILOT_REPLAN_TRIGGER", "detail must be a non-empty bounded string")
    if not isinstance(evidence, list) or not all(isinstance(item, dict) for item in evidence) or len(evidence) > 64:
        raise AutopilotPlannerError("ERROR_AUTOPILOT_REPLAN_TRIGGER", "evidence must be a bounded array of objects")
    return {"kind": kind, "detail": detail.strip(), "evidence": deepcopy(evidence)}


def replan_autopilot(
    checkpoint_id: str,
    *,
    trigger: dict[str, Any],
    provider: str | None = None,
    model: str | None = None,
    cfg: Settings = settings,
) -> dict[str, Any]:
    parent = get_checkpoint(checkpoint_id, cfg)
    if parent is None:
        raise AutopilotPlannerError("ERROR_CHECKPOINT_NOT_FOUND", checkpoint_id)
    if parent["state"] not in {"PAUSED_PROVIDER", "PAUSED_RESOURCE", "BLOCKED_EXTERNAL", "BLOCKED_PERMISSION", "FAILED"}:
        raise AutopilotPlannerError(
            "ERROR_AUTOPILOT_REPLAN_STATE",
            f"replan requires a paused, blocked, or failed checkpoint; observed {parent['state']}",
        )
    normalized_trigger = _validate_replan_trigger(trigger)
    state = deepcopy((parent.get("metadata") or {}).get("autopilot"))
    if not isinstance(state, dict):
        raise AutopilotPlannerError("ERROR_AUTOPILOT_STATE_MISSING", checkpoint_id)
    budgets = state.get("budgets")
    counters = state.get("counters")
    if not isinstance(budgets, dict) or not isinstance(counters, dict):
        raise AutopilotPlannerError("ERROR_AUTOPILOT_STATE_MISSING", checkpoint_id)
    max_replans = budgets.get("max_replans")
    next_replan = int(counters.get("replans") or 0) + 1
    if not isinstance(max_replans, int) or isinstance(max_replans, bool) or max_replans < next_replan:
        raise AutopilotPlannerError(
            "ERROR_AUTOPILOT_REPLAN_BUDGET",
            f"bounded replanning requires max_replans >= {next_replan}",
        )

    try:
        prior_dag = get_task_dag_state(checkpoint_id, cfg)
    except CheckpointError as exc:
        raise AutopilotPlannerError(exc.status, exc.detail) from exc

    selected_provider = provider if provider is not None else parent.get("provider")
    selected_model = model if model is not None else parent.get("model")
    payload, model_receipt = _model_plan(
        objective=parent["objective"],
        source_sha=parent["source_sha"],
        provider=selected_provider,
        model=selected_model,
        prior_dag=prior_dag,
        trigger=normalized_trigger,
    )
    try:
        planned = plan_autopilot(
            session_id=parent["session_id"],
            run_id=parent["run_id"],
            objective=parent["objective"],
            source_sha=parent["source_sha"],
            task_definitions=payload["tasks"],
            budgets=budgets,
            provider=selected_provider,
            model=selected_model,
            cfg=cfg,
        )
    except AutopilotError as exc:
        raise AutopilotPlannerError(exc.status, exc.detail) from exc

    successor = planned["checkpoint"]
    successor_state = deepcopy((successor.get("metadata") or {}).get("autopilot"))
    successor_state["counters"] = deepcopy(counters)
    successor_state["counters"]["replans"] = next_replan
    successor_state["started_at"] = state.get("started_at")
    successor_state["fingerprint_history"] = deepcopy(state.get("fingerprint_history") or [])
    successor_state["no_progress_streak"] = int(state.get("no_progress_streak") or 0)
    successor = set_checkpoint_metadata_namespace(
        successor["checkpoint_id"],
        namespace="autopilot",
        value=successor_state,
        expected_sequence=successor["sequence"],
        cfg=cfg,
    )
    replan_meta = {
        "schema_version": REPLAN_SCHEMA_VERSION,
        "parent_checkpoint_id": parent["checkpoint_id"],
        "trigger": normalized_trigger,
        "trigger_sha256": canonical_sha256(normalized_trigger),
        "prior_definition_sha256": prior_dag["definition_sha256"],
        "usage_offset": deepcopy(counters),
        "replan_number": next_replan,
        "created_at": utc_now(),
    }
    replan_meta["receipt_sha256"] = canonical_sha256(replan_meta)
    successor = set_checkpoint_metadata_namespace(
        successor["checkpoint_id"],
        namespace=REPLAN_NAMESPACE,
        value=replan_meta,
        expected_sequence=successor["sequence"],
        cfg=cfg,
    )
    successor = _attach_plan_receipt(
        successor,
        payload=payload,
        model_receipt=model_receipt,
        parent_checkpoint_id=parent["checkpoint_id"],
        trigger=normalized_trigger,
        cfg=cfg,
    )
    return {
        "status": "AUTOPILOT_REPLAN_READY",
        "checkpoint": successor,
        "task_dag": get_task_dag_state(successor["checkpoint_id"], cfg),
        "rationale": payload.get("rationale") or "",
        "replan_receipt": successor["metadata"][REPLAN_NAMESPACE],
        "plan_receipt": successor["metadata"][MODEL_PLAN_NAMESPACE],
    }
