from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import jsonschema

from .agent_checkpoints import (
    CheckpointError,
    get_checkpoint,
    get_task_dag_state,
    list_checkpoints,
    set_checkpoint_metadata_namespace,
    transition_checkpoint,
)
from .agent_task_dag import TaskDagError, build_task_dag
from .autopilot import AutopilotError, plan_autopilot
from .config import Settings, settings
from .model_router import ModelProviderError, generate_text
from .model_routing_policy import ModelRoutingError, plan_model_route
from .tools import TOOL_REGISTRY
from .utils import canonical_sha256, stable_id, utc_now

PLAN_PROPOSAL_SCHEMA_VERSION = "mucaro-autopilot-plan-proposal/1.0"
PLANNING_RECEIPT_SCHEMA_VERSION = "mucaro-autopilot-planning-receipt/1.0"
PLANNING_NAMESPACE = "autopilot_planning"
MAX_MODEL_RESPONSE_CHARS = 120_000
MAX_ACTION_ARGUMENT_BYTES = 64 * 1024
DEFAULT_MODEL_PLANNER_REPLANS = 2
ACTIVE_TASK_STATES = {"RUNNING", "AWAITING_APPROVAL", "AWAITING_INPUT"}


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AutopilotPlannerError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "schemas" / "autopilot_plan_proposal.schema.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AutopilotPlannerError("ERROR_AUTOPILOT_PLANNER_SCHEMA_UNAVAILABLE", f"{path}: {exc}") from exc


def _validate_proposal(value: Any, *, expected_kind: str, objective: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AutopilotPlannerError("ERROR_AUTOPILOT_PLAN_SHAPE", "model plan must be a JSON object")
    errors = sorted(
        jsonschema.Draft202012Validator(_schema()).iter_errors(value),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        where = "/".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise AutopilotPlannerError("ERROR_AUTOPILOT_PLAN_SCHEMA", f"{where}: {errors[0].message}")
    if value["kind"] != expected_kind:
        raise AutopilotPlannerError(
            "ERROR_AUTOPILOT_PLAN_KIND",
            f"expected {expected_kind}, observed {value['kind']}",
        )
    if value["objective"] != objective:
        raise AutopilotPlannerError(
            "ERROR_AUTOPILOT_PLAN_OBJECTIVE_DRIFT",
            "model output changed the operator objective",
        )
    return deepcopy(value)


def _tool_catalog() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, tool in sorted(TOOL_REGISTRY.items()):
        if not tool.autopilot_success_statuses:
            continue
        rows.append(
            {
                "name": name,
                "description": tool.description,
                "mutates_state": tool.mutates_state,
                "network": tool.network,
                "success_statuses": list(tool.autopilot_success_statuses),
            }
        )
    return rows


def _parse_model_json(text: str, *, expected_kind: str, objective: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise AutopilotPlannerError("ERROR_AUTOPILOT_PLAN_EMPTY", "model returned no plan text")
    if len(text) > MAX_MODEL_RESPONSE_CHARS:
        raise AutopilotPlannerError(
            "ERROR_AUTOPILOT_PLAN_RESPONSE_TOO_LARGE",
            f"model response exceeds {MAX_MODEL_RESPONSE_CHARS} characters",
        )
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AutopilotPlannerError(
            "ERROR_AUTOPILOT_PLAN_JSON",
            f"model response must be strict JSON with no markdown wrapper: {exc}",
        ) from exc
    return _validate_proposal(value, expected_kind=expected_kind, objective=objective)


def _task_definitions(proposal: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    definitions: list[dict[str, Any]] = []
    action_hashes: dict[str, str | None] = {}
    for task in proposal["tasks"]:
        action = task["action"]
        metadata: dict[str, Any] = {"planner": {"source": "MODEL_PROPOSAL"}}
        if action is not None:
            tool_name = action["tool_name"]
            tool = TOOL_REGISTRY.get(tool_name)
            if tool is None:
                raise AutopilotPlannerError(
                    "ERROR_AUTOPILOT_PLAN_TOOL_UNAVAILABLE",
                    f"{task['id']}:{tool_name}",
                )
            if not tool.autopilot_success_statuses:
                raise AutopilotPlannerError(
                    "ERROR_AUTOPILOT_PLAN_TOOL_SUCCESS_CONTRACT",
                    f"{task['id']}:{tool_name}",
                )
            try:
                encoded = json.dumps(
                    action["arguments"],
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise AutopilotPlannerError(
                    "ERROR_AUTOPILOT_PLAN_ARGUMENTS_JSON",
                    task["id"],
                ) from exc
            if len(encoded) > MAX_ACTION_ARGUMENT_BYTES:
                raise AutopilotPlannerError(
                    "ERROR_AUTOPILOT_PLAN_ARGUMENTS_TOO_LARGE",
                    f"{task['id']}:{len(encoded)}>{MAX_ACTION_ARGUMENT_BYTES}",
                )
            metadata["action"] = {
                "tool_name": tool_name,
                "arguments": deepcopy(action["arguments"]),
            }
            action_hashes[task["id"]] = canonical_sha256(metadata["action"])
        else:
            action_hashes[task["id"]] = None
        definitions.append(
            {
                "id": task["id"],
                "title": task["title"],
                "description": task["description"],
                "depends_on": list(task["depends_on"]),
                "metadata": metadata,
            }
        )
    try:
        dag = build_task_dag(definitions)
    except TaskDagError as exc:
        raise AutopilotPlannerError(exc.status, exc.detail) from exc
    return definitions, {
        "definition_sha256": dag["definition_sha256"],
        "task_count": dag["task_count"],
        "edge_count": dag["edge_count"],
        "topological_order": list(dag["topological_order"]),
        "action_sha256_by_task": action_hashes,
    }


def _planner_prompts(
    *,
    objective: str,
    kind: str,
    context: dict[str, Any],
    trigger: dict[str, Any] | None,
) -> tuple[str, str]:
    catalog = _tool_catalog()
    system = (
        "You are Mucaro's bounded planning model. Return one strict JSON object only; "
        "do not use markdown fences or prose outside JSON. The object must conform to "
        "mucaro-autopilot-plan-proposal/1.0. Planning grants no permission and executes "
        "nothing. Use only tools in the supplied catalog. Never invent repository paths, "
        "hashes, credentials, approvals, network authority, or mutation authority. If a "
        "required concrete argument is unknown, use action:null so the canonical runtime "
        "will require operator input. Preserve the objective byte-for-byte. Dependencies "
        "must form an acyclic graph. Every tool action must use concrete JSON arguments."
    )
    user_payload = {
        "required_schema_version": PLAN_PROPOSAL_SCHEMA_VERSION,
        "required_kind": kind,
        "objective": objective,
        "context": context,
        "replan_trigger": trigger,
        "allowed_tools": catalog,
        "output_shape": {
            "schema_version": PLAN_PROPOSAL_SCHEMA_VERSION,
            "kind": kind,
            "objective": objective,
            "rationale": "brief planning rationale",
            "tasks": [
                {
                    "id": "stable_task_id",
                    "title": "task title",
                    "description": "bounded task description",
                    "depends_on": [],
                    "action": {"tool_name": "catalog.tool", "arguments": {}} ,
                }
            ],
        },
    }
    return system, json.dumps(user_payload, sort_keys=True, ensure_ascii=False)


def _route_and_generate(
    *,
    objective: str,
    kind: str,
    context: dict[str, Any],
    trigger: dict[str, Any] | None,
    provider: str | None,
    model: str | None,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    preferred = provider.strip() if isinstance(provider, str) and provider.strip() and provider.strip() != "mock" else None
    preferred_model = model if preferred is not None else None
    try:
        route = plan_model_route(
            task_type="coding",
            preferred_provider=preferred,
            preferred_model=preferred_model,
            allowed_providers=["openai_compatible_local", "anthropic"],
            allow_live=os.getenv("MUCARO_AI_LIVE_MODE") == "1",
            require_citations=False,
            required_modalities=["text"],
        )
    except ModelRoutingError as exc:
        raise AutopilotPlannerError(exc.status, exc.detail) from exc
    if route["status"] != "MODEL_ROUTE_READY" or not route.get("selected_provider"):
        raise AutopilotPlannerError(
            "ERROR_AUTOPILOT_PLANNER_MODEL_ROUTE",
            str(route.get("selection_reason") or "no eligible planning model"),
        )
    system, user = _planner_prompts(
        objective=objective,
        kind=kind,
        context=context,
        trigger=trigger,
    )
    try:
        generated = generate_text(
            system,
            user,
            model_provider=route["selected_provider"],
            model_name=route.get("selected_model"),
            max_tokens=2400,
            temperature=0.0,
        )
    except ModelProviderError as exc:
        raise AutopilotPlannerError(exc.status, exc.detail) from exc
    raw = generated["text"]
    proposal = _parse_model_json(raw, expected_kind=kind, objective=objective)
    return route, generated, raw


def _planning_receipt(
    *,
    kind: str,
    objective: str,
    source_sha: str,
    proposal: dict[str, Any],
    raw_response: str,
    route: dict[str, Any],
    generated: dict[str, Any],
    dag_summary: dict[str, Any],
    trigger_sha256: str | None,
    predecessor_checkpoint_id: str | None,
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "schema_version": PLANNING_RECEIPT_SCHEMA_VERSION,
        "kind": kind,
        "objective_sha256": _text_sha256(objective),
        "source_sha": source_sha,
        "provider": generated["model_provider"],
        "model": generated["model_name"],
        "model_route_sha256": route.get("route_sha256"),
        "raw_response_sha256": _text_sha256(raw_response),
        "proposal_sha256": canonical_sha256(proposal),
        "dag_definition_sha256": dag_summary["definition_sha256"],
        "task_count": dag_summary["task_count"],
        "edge_count": dag_summary["edge_count"],
        "topological_order": list(dag_summary["topological_order"]),
        "action_sha256_by_task": dict(dag_summary["action_sha256_by_task"]),
        "trigger_sha256": trigger_sha256,
        "predecessor_checkpoint_id": predecessor_checkpoint_id,
        "model_usage": deepcopy(generated.get("model_usage")),
        "created_at": utc_now(),
        "invariants": {
            "strict_json_parsed": True,
            "schema_validated": True,
            "objective_preserved": True,
            "tool_catalog_enforced": True,
            "tool_success_contracts_enforced": True,
            "argument_payloads_bounded": True,
            "canonical_dag_validated": True,
            "planning_grants_no_authority": True,
        },
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def _persist_planning_namespace(
    checkpoint: dict[str, Any],
    *,
    history: list[dict[str, Any]],
    predecessor_checkpoint_id: str | None,
    trigger_sha256: str | None,
    cfg: Settings,
) -> dict[str, Any]:
    namespace = {
        "schema_version": "mucaro-autopilot-planning-history/1.0",
        "predecessor_checkpoint_id": predecessor_checkpoint_id,
        "trigger_sha256": trigger_sha256,
        "history": deepcopy(history),
        "history_sha256": canonical_sha256(history),
    }
    try:
        checkpoint = set_checkpoint_metadata_namespace(
            checkpoint["checkpoint_id"],
            namespace=PLANNING_NAMESPACE,
            value=namespace,
            expected_sequence=checkpoint["sequence"],
            cfg=cfg,
        )
        checkpoint = transition_checkpoint(
            checkpoint["checkpoint_id"],
            state="READY_FOR_APPROVAL",
            expected_sequence=checkpoint["sequence"],
            cfg=cfg,
        )
    except CheckpointError as exc:
        raise AutopilotPlannerError(exc.status, exc.detail) from exc
    return checkpoint


def _normalize_context(context: dict[str, Any], *, source_sha: str) -> dict[str, Any]:
    if not isinstance(context, dict):
        raise AutopilotPlannerError("ERROR_AUTOPILOT_PLANNER_CONTEXT", "context must be an object")
    result = deepcopy(context)
    repository = result.get("repository")
    if not isinstance(repository, dict):
        raise AutopilotPlannerError(
            "ERROR_AUTOPILOT_PLANNER_REPOSITORY_CONTEXT",
            "a governed repository status receipt is required",
        )
    if repository.get("head_sha") != source_sha:
        raise AutopilotPlannerError(
            "ERROR_AUTOPILOT_PLANNER_SOURCE_DRIFT",
            f"repository head {repository.get('head_sha')} does not match source_sha {source_sha}",
        )
    if not isinstance(repository.get("repo_root"), str) or not repository["repo_root"]:
        raise AutopilotPlannerError("ERROR_AUTOPILOT_PLANNER_REPOSITORY_CONTEXT", "repo_root is required")
    return result


def plan_autopilot_from_model(
    *,
    session_id: str,
    run_id: str,
    objective: str,
    source_sha: str,
    context: dict[str, Any],
    budgets: dict[str, Any] | None = None,
    provider: str | None = None,
    model: str | None = None,
    cfg: Settings = settings,
) -> dict[str, Any]:
    normalized_objective = objective.strip()
    if not normalized_objective:
        raise AutopilotPlannerError("ERROR_AUTOPILOT_PLANNER_OBJECTIVE", "objective is required")
    normalized_context = _normalize_context(context, source_sha=source_sha)
    route, generated, raw = _route_and_generate(
        objective=normalized_objective,
        kind="INITIAL",
        context=normalized_context,
        trigger=None,
        provider=provider,
        model=model,
    )
    proposal = _parse_model_json(raw, expected_kind="INITIAL", objective=normalized_objective)
    definitions, dag_summary = _task_definitions(proposal)
    effective_budgets = {"max_replans": DEFAULT_MODEL_PLANNER_REPLANS}
    if budgets:
        effective_budgets.update(budgets)
    receipt = _planning_receipt(
        kind="INITIAL",
        objective=normalized_objective,
        source_sha=source_sha,
        proposal=proposal,
        raw_response=raw,
        route=route,
        generated=generated,
        dag_summary=dag_summary,
        trigger_sha256=None,
        predecessor_checkpoint_id=None,
    )
    try:
        planned = plan_autopilot(
            session_id=session_id,
            run_id=run_id,
            objective=normalized_objective,
            source_sha=source_sha,
            task_definitions=definitions,
            budgets=effective_budgets,
            provider=generated["model_provider"],
            model=generated["model_name"],
            initial_replans=0,
            cfg=cfg,
        )
    except AutopilotError as exc:
        raise AutopilotPlannerError(exc.status, exc.detail) from exc
    checkpoint = _persist_planning_namespace(
        planned["checkpoint"],
        history=[receipt],
        predecessor_checkpoint_id=None,
        trigger_sha256=None,
        cfg=cfg,
    )
    return {
        "status": "AUTOPILOT_MODEL_PLAN_READY",
        "checkpoint": checkpoint,
        "task_dag": get_task_dag_state(checkpoint["checkpoint_id"], cfg),
        "planning_receipt": receipt,
        "proposal": proposal,
    }


def _replan_trigger(checkpoint: dict[str, Any], dag: dict[str, Any]) -> dict[str, Any]:
    active = sorted(task["id"] for task in dag["tasks"] if task["state"] in ACTIVE_TASK_STATES)
    if active:
        raise AutopilotPlannerError(
            "ERROR_AUTOPILOT_REPLAN_ACTIVE_TASK",
            ",".join(active),
        )
    residue = []
    for task in dag["tasks"]:
        if task["state"] not in {"FAILED", "BLOCKED"}:
            continue
        residue.append(
            {
                "id": task["id"],
                "state": task["state"],
                "attempt_count": task["attempt_count"],
                "blocked_reason": task.get("blocked_reason"),
                "evidence_sha256": canonical_sha256(task.get("evidence") or []),
                "artifacts_sha256": canonical_sha256(task.get("artifacts") or []),
            }
        )
    if not residue:
        raise AutopilotPlannerError(
            "ERROR_AUTOPILOT_REPLAN_TRIGGER",
            "replanning requires FAILED or BLOCKED task residue",
        )
    open_contradictions = [
        item.get("id")
        for item in checkpoint.get("contradictions") or []
        if item.get("state") != "ADJUDICATED"
    ]
    if open_contradictions:
        raise AutopilotPlannerError(
            "ERROR_AUTOPILOT_REPLAN_CONTRADICTION",
            ",".join(str(item) for item in open_contradictions),
        )
    return {
        "predecessor_checkpoint_id": checkpoint["checkpoint_id"],
        "predecessor_run_id": checkpoint["run_id"],
        "predecessor_sequence": checkpoint["sequence"],
        "predecessor_source_sha": checkpoint["source_sha"],
        "predecessor_dag_definition_sha256": dag["definition_sha256"],
        "residue": residue,
        "contradictions_sha256": canonical_sha256(checkpoint.get("contradictions") or []),
    }


def _planning_history(checkpoint: dict[str, Any]) -> list[dict[str, Any]]:
    namespace = (checkpoint.get("metadata") or {}).get(PLANNING_NAMESPACE)
    if not isinstance(namespace, dict):
        return []
    history = namespace.get("history")
    if not isinstance(history, list):
        raise AutopilotPlannerError("ERROR_AUTOPILOT_PLANNING_HISTORY", "planning history is malformed")
    for receipt in history:
        if not isinstance(receipt, dict) or receipt.get("schema_version") != PLANNING_RECEIPT_SCHEMA_VERSION:
            raise AutopilotPlannerError("ERROR_AUTOPILOT_PLANNING_HISTORY", "planning receipt is malformed")
        expected = receipt.get("receipt_sha256")
        probe = dict(receipt)
        probe.pop("receipt_sha256", None)
        if expected != canonical_sha256(probe):
            raise AutopilotPlannerError("ERROR_AUTOPILOT_PLANNING_HISTORY_DRIFT", str(expected))
    return deepcopy(history)


def replan_autopilot_from_model(
    checkpoint_id: str,
    *,
    source_sha: str,
    context: dict[str, Any],
    provider: str | None = None,
    model: str | None = None,
    cfg: Settings = settings,
) -> dict[str, Any]:
    checkpoint = get_checkpoint(checkpoint_id, cfg)
    if checkpoint is None:
        raise AutopilotPlannerError("ERROR_CHECKPOINT_NOT_FOUND", checkpoint_id)
    if checkpoint["state"] == "COMPLETED":
        raise AutopilotPlannerError("ERROR_AUTOPILOT_REPLAN_COMPLETED", checkpoint_id)

    dag = get_task_dag_state(checkpoint_id, cfg)
    trigger = _replan_trigger(checkpoint, dag)
    trigger_sha256 = canonical_sha256(trigger)

    for candidate in list_checkpoints(cfg):
        namespace = (candidate.get("metadata") or {}).get(PLANNING_NAMESPACE)
        if (
            isinstance(namespace, dict)
            and namespace.get("predecessor_checkpoint_id") == checkpoint_id
            and namespace.get("trigger_sha256") == trigger_sha256
        ):
            raise AutopilotPlannerError(
                "ERROR_AUTOPILOT_REPLAN_DUPLICATE",
                f"successor already exists: {candidate['checkpoint_id']}",
            )

    autopilot_state = (checkpoint.get("metadata") or {}).get("autopilot")
    if not isinstance(autopilot_state, dict):
        raise AutopilotPlannerError("ERROR_AUTOPILOT_STATE_MISSING", checkpoint_id)
    budgets = deepcopy(autopilot_state.get("budgets") or {})
    counters = autopilot_state.get("counters") or {}
    prior_replans = int(counters.get("replans") or 0)
    limit = budgets.get("max_replans")
    if limit is not None and prior_replans >= int(limit):
        raise AutopilotPlannerError(
            "ERROR_AUTOPILOT_REPLAN_BUDGET",
            f"replan count {prior_replans} reached max_replans {limit}",
        )

    normalized_context = _normalize_context(context, source_sha=source_sha)
    model_context = {
        **normalized_context,
        "predecessor": trigger,
    }
    objective = checkpoint["objective"]
    route, generated, raw = _route_and_generate(
        objective=objective,
        kind="REPLAN",
        context=model_context,
        trigger=trigger,
        provider=provider or checkpoint.get("provider"),
        model=model or checkpoint.get("model"),
    )
    proposal = _parse_model_json(raw, expected_kind="REPLAN", objective=objective)
    definitions, dag_summary = _task_definitions(proposal)
    receipt = _planning_receipt(
        kind="REPLAN",
        objective=objective,
        source_sha=source_sha,
        proposal=proposal,
        raw_response=raw,
        route=route,
        generated=generated,
        dag_summary=dag_summary,
        trigger_sha256=trigger_sha256,
        predecessor_checkpoint_id=checkpoint_id,
    )
    history = _planning_history(checkpoint)
    history.append(receipt)

    if checkpoint["state"] != "FAILED":
        try:
            checkpoint = transition_checkpoint(
                checkpoint_id,
                state="FAILED",
                expected_sequence=checkpoint["sequence"],
                cfg=cfg,
            )
        except CheckpointError as exc:
            raise AutopilotPlannerError(exc.status, exc.detail) from exc

    successor_run_id = stable_id(
        "autopilot_replan",
        checkpoint["run_id"],
        trigger_sha256,
        source_sha,
        length=16,
    )
    try:
        planned = plan_autopilot(
            session_id=checkpoint["session_id"],
            run_id=successor_run_id,
            objective=objective,
            source_sha=source_sha,
            task_definitions=definitions,
            budgets=budgets,
            provider=generated["model_provider"],
            model=generated["model_name"],
            initial_replans=prior_replans + 1,
            cfg=cfg,
        )
    except AutopilotError as exc:
        raise AutopilotPlannerError(exc.status, exc.detail) from exc
    successor = _persist_planning_namespace(
        planned["checkpoint"],
        history=history,
        predecessor_checkpoint_id=checkpoint_id,
        trigger_sha256=trigger_sha256,
        cfg=cfg,
    )
    return {
        "status": "AUTOPILOT_MODEL_REPLAN_READY",
        "checkpoint": successor,
        "predecessor_checkpoint": checkpoint,
        "task_dag": get_task_dag_state(successor["checkpoint_id"], cfg),
        "planning_receipt": receipt,
        "proposal": proposal,
        "trigger": trigger,
    }
