from __future__ import annotations

from typing import Any

from .agent_checkpoints import CheckpointError, get_task_dag_state, list_checkpoints
from .config import Settings, settings
from .conversations import get_conversation, list_conversations
from .permission_leases import PermissionLeaseError, list_permission_leases
from .response_flow import get_pending_interaction
from .tools import list_tool_calls
from .utils import utc_now
from .work_runs import get_run, list_run_events, list_runs

TERMINAL_VIEWS = ("current", "sessions", "issues", "pull_requests", "gists")
GITHUB_LIST_TOOLS = {
    "issues": "github.issues_list",
    "pull_requests": "github.prs_list",
    "gists": "github.gists_list",
}


class TerminalProjectionError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _latest_github_result(view: str, calls: list[dict[str, Any]]) -> dict[str, Any] | None:
    tool_name = GITHUB_LIST_TOOLS.get(view)
    if tool_name is None:
        return None
    candidates = [
        call
        for call in calls
        if call.get("tool_name") == tool_name
        and call.get("status") == "EXECUTED"
        and isinstance(call.get("result"), dict)
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda call: (
            str(call.get("executed_at") or ""),
            str(call.get("tool_call_id") or ""),
        ),
        reverse=True,
    )
    call = candidates[0]
    result = dict(call["result"])
    return {
        "tool_call_id": call.get("tool_call_id"),
        "executed_at": call.get("executed_at"),
        "result": result,
        "retrieved_utc": result.get("retrieved_utc"),
    }


def _latest_tool_result(tool_name: str, calls: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [
        call
        for call in calls
        if call.get("tool_name") == tool_name
        and call.get("status") == "EXECUTED"
        and isinstance(call.get("result"), dict)
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda call: (
            str(call.get("executed_at") or ""),
            str(call.get("tool_call_id") or ""),
        ),
        reverse=True,
    )
    call = candidates[0]
    return {
        "tool_call_id": call.get("tool_call_id"),
        "executed_at": call.get("executed_at"),
        "result": dict(call["result"]),
    }


def _checkpoint_projection(
    conversation_id: str | None,
    run_id: str | None,
    cfg: Settings,
) -> dict[str, Any] | None:
    try:
        checkpoints = list_checkpoints(cfg)
    except CheckpointError as exc:
        return {
            "status": "ERROR",
            "error": {"status": exc.status, "detail": exc.detail},
        }

    matches = [
        checkpoint
        for checkpoint in checkpoints
        if (
            (conversation_id and checkpoint.get("session_id") == conversation_id)
            or (run_id and checkpoint.get("run_id") == run_id)
        )
    ]
    if not matches:
        return None
    matches.sort(
        key=lambda checkpoint: (
            str(checkpoint.get("updated_at") or ""),
            int(checkpoint.get("sequence") or 0),
        ),
        reverse=True,
    )
    checkpoint = matches[0]
    task_dag = None
    if isinstance((checkpoint.get("metadata") or {}).get("task_dag"), dict):
        try:
            task_dag = get_task_dag_state(str(checkpoint["checkpoint_id"]), cfg)
        except CheckpointError as exc:
            task_dag = {
                "status": "ERROR",
                "error": {"status": exc.status, "detail": exc.detail},
            }
    return {
        "checkpoint_id": checkpoint.get("checkpoint_id"),
        "session_id": checkpoint.get("session_id"),
        "run_id": checkpoint.get("run_id"),
        "state": checkpoint.get("state"),
        "sequence": checkpoint.get("sequence"),
        "objective": checkpoint.get("objective"),
        "provider": checkpoint.get("provider"),
        "model": checkpoint.get("model"),
        "pause_reason": checkpoint.get("pause_reason"),
        "contradictions": list(checkpoint.get("contradictions") or []),
        "task_dag": task_dag,
        "autopilot": dict((checkpoint.get("metadata") or {}).get("autopilot") or {}),
        "autopilot_planning": dict((checkpoint.get("metadata") or {}).get("autopilot_planning") or {}),
        "updated_at": checkpoint.get("updated_at"),
    }


def build_terminal_snapshot(
    *,
    conversation_id: str | None = None,
    view: str = "current",
    cfg: Settings = settings,
) -> dict[str, Any]:
    if view not in TERMINAL_VIEWS:
        raise TerminalProjectionError(
            "ERROR_TUI_VIEW",
            f"view must be one of {TERMINAL_VIEWS}",
        )

    conversations = list_conversations(cfg)
    selected_id = conversation_id
    if selected_id is None and conversations:
        selected_id = str(conversations[0]["conversation_id"])
    selected = get_conversation(selected_id, cfg) if selected_id else None
    if selected_id and selected is None:
        raise TerminalProjectionError("ERROR_CONVERSATION_NOT_FOUND", selected_id)

    run_summaries = (
        list_runs(conversation_id=selected_id, limit=20, cfg=cfg)["runs"]
        if selected_id
        else []
    )
    latest_summary = run_summaries[0] if run_summaries else None
    latest_run = (
        get_run(str(latest_summary["run_id"]), cfg)
        if latest_summary is not None
        else None
    )
    run_id = str(latest_run["run_id"]) if latest_run else None
    work_plan = None
    if run_id:
        events = list_run_events(run_id, cfg=cfg, limit=5000)
        plans = [
            event.get("data")
            for event in events
            if event.get("type") == "plan" and isinstance(event.get("data"), dict)
        ]
        work_plan = dict(plans[-1]) if plans else None

    calls = list_tool_calls(cfg)["tool_calls"]
    relevant_run_ids = {value for value in (run_id, f"tui:{selected_id}" if selected_id else None) if value}
    relevant_calls = [
        call
        for call in calls
        if call.get("run_id") in relevant_run_ids
    ]
    pending_calls = [
        call
        for call in relevant_calls
        if call.get("status") == "PENDING_APPROVAL"
    ]

    leases: list[dict[str, Any]] = []
    lease_errors: list[dict[str, str]] = []
    for relevant_run_id in sorted(relevant_run_ids):
        try:
            leases.extend(
                list_permission_leases(run_id=relevant_run_id, cfg=cfg)["leases"]
            )
        except PermissionLeaseError as exc:
            lease_errors.append({"status": exc.status, "detail": exc.detail})

    github = _latest_github_result(view, calls)
    checkpoint = _checkpoint_projection(selected_id, run_id, cfg)
    pending_interaction = (
        get_pending_interaction(selected_id, cfg)
        if selected_id
        else None
    )

    context_usage = None
    if latest_run and isinstance(latest_run.get("context_usage"), dict):
        context_usage = dict(latest_run["context_usage"])

    return {
        "schema_version": "mucaro-terminal-snapshot/1.0",
        "created_at": utc_now(),
        "view": view,
        "conversation_id": selected_id,
        "conversation": selected,
        "sessions": conversations,
        "runs": run_summaries,
        "current_run": latest_run,
        "work_plan": work_plan,
        "pending_tool_calls": pending_calls,
        "permission_leases": leases,
        "permission_lease_errors": lease_errors,
        "checkpoint": checkpoint,
        "pending_interaction": pending_interaction,
        "github": github,
        "context_usage": context_usage,
        "configured_model": {
            "provider": cfg.default_model_provider,
            "name": cfg.default_model_name,
        },
        "repository_status": _latest_tool_result("coding.repo_status", relevant_calls),
        "invariants": {
            "canonical_conversation_source": "backend.app.conversations",
            "canonical_run_source": "backend.app.work_runs",
            "canonical_tool_source": "backend.app.tools",
            "canonical_checkpoint_source": "backend.app.agent_checkpoints",
            "github_projection_from_governed_tool_receipts": True,
            "remote_fetch_performed_by_projection": False,
        },
    }
