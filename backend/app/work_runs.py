"""Conversational execution bus.

Every message a user sends becomes a *work run*: a persisted state machine
whose progress is an append-only, ordered event log. The UI renders that log
as the chronological work feed (TGT-002) and can resume from any sequence
number after a reconnect.

States::

    QUEUED -> RUNNING -> (AWAITING_APPROVAL | AWAITING_INPUT) -> RUNNING
           -> COMPLETED | FAILED | INTERRUPTED
    QUEUED -> CANCELLED

Guarantees (MOB F02-F04, F07-F09, F13-F16, F18; TGT-014, TGT-018):

- One active run per conversation; later messages queue FIFO and are
  dequeued exactly once. Queued messages can be edited or cancelled.
- A client-supplied idempotency key returns the original run on retry.
- Tool actions go through the same approval-gated tool runtime as the API;
  approvals bind to the call. ``auto`` mode may pre-approve only local,
  non-mutating tools; everything else still waits for the user.
- Tool outcomes (executed, failed, denied, cancelled) are reported one per
  call and aggregated from the call records, never from narration.
- A failed action is never retried implicitly; retry asks the user and
  creates a new call that needs a fresh approval. Successful calls are not
  repeated.
- Stop halts before the next step and records INTERRUPTED, listing any
  call that was in flight. A process restart marks RUNNING runs
  INTERRUPTED; runs waiting on approval or input resume after restart.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from typing import Any

from . import agent
from .config import Settings, settings
from .conversations import append_turns, build_conversation_context, get_conversation
from .db import ensure_db, get_connection
from .error_categories import classify_error
from .model_router import ModelProviderError
from .model_routing_policy import ModelRoutingError, plan_model_route
from .response_flow import (
    InteractionError,
    build_response_envelope,
    create_interaction,
    dismiss_interaction,
    get_interaction,
    get_pending_interaction,
    make_artifact_part,
    make_question_part,
    make_task_receipt_part,
    make_text_part,
    make_tool_call_part,
    make_tool_result_part,
)
from .services import answer_question
from .tools import (
    TOOL_REGISTRY,
    ToolRuntimeError,
    approve_tool_call,
    cancel_tool_call,
    list_tool_calls,
    request_tool_call,
    run_tool_call,
    summarize_tool_calls,
)
from .utils import stable_id, utc_now

RUN_STATES = ("QUEUED", "RUNNING", "AWAITING_APPROVAL", "AWAITING_INPUT", "COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED")
ACTIVE_STATES = {"RUNNING", "AWAITING_APPROVAL", "AWAITING_INPUT"}
TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"}
MODES = {
    "agent": "Claude works on its own: web search, its code sandbox and read-only local actions run; changes to files or memory still ask.",
    "plan": "Show the plan and proposed actions; execute nothing.",
    "ask": "Ask before every action (default).",
    "auto": "Pre-approve local read-only actions; anything that changes state or uses the network still asks.",
}
EVENT_TYPES = {
    "queued", "run_started", "route", "plan", "tool_call", "approval_request", "tool_started", "tool_result",
    "tool_summary", "question", "question_resolved", "retry_requested", "user_note", "retrieval", "text_delta",
    "citation", "artifact", "warning", "error", "interrupted", "cancelled", "final", "progress", "usage",
}
# Modes in which slash commands that are local and read-only run without asking.
AUTO_APPROVE_MODES = {"auto", "agent"}
# What an action that never ran tells Claude, by why its turn ended.
SALVAGE_REASONS = {
    "USER_STOP": "Not run: the user stopped this turn.",
    "SUPERSEDED_BY_NEW_MESSAGE": "Not run: the user sent a new message instead.",
    "PROCESS_RESTART": "Not run: the app restarted during this turn.",
    "FAILED": "Not run: this turn failed.",
}
MAX_PROMPT_CHARS = 20_000
TEXT_DELTA_CHARS = 160

# Slash commands are explicit user-invoked tool requests, not model autonomy.
COMMANDS: dict[str, dict[str, Any]] = {
    "/status": {"tool": "source_library.status", "usage": "/status", "arg": None},
    "/audit": {"tool": "product.audit", "usage": "/audit", "arg": None},
    "/memory": {"tool": "memory.list", "usage": "/memory", "arg": None},
    "/repo": {"tool": "coding.repo_status", "usage": "/repo <repo_root>", "arg": "repo_root"},
    "/diff": {"tool": "coding.diff", "usage": "/diff <repo_root>", "arg": "repo_root"},
    "/fetch": {"tool": "web.fetch", "usage": "/fetch <url>", "arg": "url"},
    "/render": {"tool": "web.render", "usage": "/render <url>", "arg": "url", "extra": {"allow_rendered_browser": True}},
    "/search": {"tool": "web.search", "usage": "/search <query>", "arg": "query", "extra": {"allow_live_search": True}},
}

_EXECUTION_MODE = {"mode": "thread"}
_RUN_LOCKS: dict[str, threading.Lock] = {}
_RUN_KICKS: set[str] = set()
_LOCKS_GUARD = threading.Lock()
_CONVERSATION_DISPATCH_LOCK = threading.Lock()
# Bumped after every committed write so event streams in this process wake at once instead of
# waiting out their poll interval; writers in other processes are still seen by the poll.
_EVENTS_COMMITTED = threading.Condition()
_COMMIT_GENERATION = [0]


class RunError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def set_execution_mode(mode: str) -> None:
    """'thread' runs steps in a background thread (app); 'inline' runs them in the caller (tests)."""
    if mode not in {"thread", "inline"}:
        raise ValueError(mode)
    _EXECUTION_MODE["mode"] = mode


def available_modes() -> dict[str, Any]:
    commands = []
    for name, spec in COMMANDS.items():
        tool = TOOL_REGISTRY[spec["tool"]]
        commands.append(
            {
                "command": name,
                "usage": spec["usage"],
                "tool": spec["tool"],
                "network": tool.network,
                "mutates_state": tool.mutates_state,
                "auto_approvable": _auto_approvable(spec["tool"]),
            }
        )
    return {"status": "RUN_MODES_OK", "default_mode": "agent", "modes": [{"mode": key, "description": value} for key, value in MODES.items()], "commands": commands}


def _auto_approvable(tool_name: str) -> bool:
    tool = TOOL_REGISTRY.get(tool_name)
    return tool is not None and not tool.mutates_state and tool.network == "none"


# --- persistence --------------------------------------------------------------


def _row_to_run(row: Any) -> dict[str, Any]:
    run = json.loads(row["payload_json"])
    run["state"] = row["state"]
    run["version"] = row["version"]
    run["queue_seq"] = row["queue_seq"]
    return run


def _load(conn: Any, run_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM work_runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise RunError("ERROR_RUN_NOT_FOUND", f"Run not found: {run_id}")
    return _row_to_run(row)


def _save(conn: Any, run: dict[str, Any]) -> dict[str, Any]:
    """Optimistic write: fails if another writer moved the run since it was loaded."""
    run["updated_at"] = utc_now()
    body = {key: value for key, value in run.items() if key not in {"state", "version", "queue_seq"}}
    cursor = conn.execute(
        "UPDATE work_runs SET state = ?, version = version + 1, payload_json = ? WHERE run_id = ? AND version = ?",
        (run["state"], json.dumps(body, ensure_ascii=False, sort_keys=True), run["run_id"], run["version"]),
    )
    if cursor.rowcount != 1:
        raise RunError("ERROR_RUN_VERSION_CONFLICT", f"Run changed concurrently: {run['run_id']}")
    run["version"] += 1
    return run


def _emit(conn: Any, run_id: str, event_type: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    if event_type not in EVENT_TYPES:
        raise RunError("ERROR_RUN_EVENT_TYPE", event_type)
    row = conn.execute("SELECT COALESCE(MAX(seq), 0) AS seq FROM run_events WHERE run_id = ?", (run_id,)).fetchone()
    seq = int(row["seq"]) + 1
    event = {"run_id": run_id, "seq": seq, "type": event_type, "created_at": utc_now(), "data": data or {}}
    conn.execute(
        "INSERT INTO run_events (run_id, seq, event_type, payload_json) VALUES (?, ?, ?, ?)",
        (run_id, seq, event_type, json.dumps(event, ensure_ascii=False, sort_keys=True)),
    )
    return event


class _Tx:
    """BEGIN IMMEDIATE transaction: serializes writers across threads and processes."""

    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg

    def __enter__(self) -> Any:
        self.conn = get_connection(self.cfg.sqlite_path)
        self.conn.isolation_level = None
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        try:
            self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.conn.close()
        if not exc_type:
            with _EVENTS_COMMITTED:
                _COMMIT_GENERATION[0] += 1
                _EVENTS_COMMITTED.notify_all()


def get_run(run_id: str, cfg: Settings = settings) -> dict[str, Any]:
    ensure_db(cfg.sqlite_path)
    with get_connection(cfg.sqlite_path) as conn:
        return _load(conn, run_id)


def list_run_events(run_id: str, *, after_seq: int = 0, limit: int = 500, cfg: Settings = settings) -> list[dict[str, Any]]:
    ensure_db(cfg.sqlite_path)
    with get_connection(cfg.sqlite_path) as conn:
        _load(conn, run_id)
        rows = conn.execute(
            "SELECT payload_json FROM run_events WHERE run_id = ? AND seq > ? ORDER BY seq ASC LIMIT ?",
            (run_id, after_seq, max(1, min(limit, 5000))),
        ).fetchall()
    return [json.loads(row["payload_json"]) for row in rows]


def _run_calls(run: dict[str, Any], cfg: Settings) -> list[dict[str, Any]]:
    wanted = run.get("tool_call_ids", [])
    by_id = {call["tool_call_id"]: call for call in list_tool_calls(cfg)["tool_calls"] if call["tool_call_id"] in set(wanted)}
    return [by_id[call_id] for call_id in wanted if call_id in by_id]


def summarize_run(run: dict[str, Any], cfg: Settings = settings) -> dict[str, Any]:
    """Background-task view (F18): metrics come from receipts and the event log."""
    calls = _run_calls(run, cfg)
    with get_connection(cfg.sqlite_path) as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM run_events WHERE run_id = ?", (run["run_id"],)).fetchone()["n"]
    return {
        "run_id": run["run_id"],
        "conversation_id": run["conversation_id"],
        "state": run["state"],
        "version": run["version"],
        "mode": run.get("mode"),
        "prompt_preview": run.get("prompt", "")[:120],
        "created_at": run.get("created_at"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "outcome": run.get("outcome"),
        "error": run.get("error"),
        "tool_summary": summarize_tool_calls(calls),
        "event_count": int(count),
        "queue_seq": run["queue_seq"],
    }


def list_runs(
    *,
    conversation_id: str | None = None,
    states: list[str] | None = None,
    limit: int = 20,
    before_seq: int | None = None,
    cfg: Settings = settings,
) -> dict[str, Any]:
    ensure_db(cfg.sqlite_path)
    clauses: list[str] = []
    params: list[Any] = []
    if conversation_id:
        clauses.append("conversation_id = ?")
        params.append(conversation_id)
    if states:
        clauses.append(f"state IN ({','.join('?' for _ in states)})")
        params.extend(states)
    if before_seq is not None:
        clauses.append("queue_seq < ?")
        params.append(before_seq)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    limit = max(1, min(limit, 100))
    with get_connection(cfg.sqlite_path) as conn:
        rows = conn.execute(f"SELECT * FROM work_runs {where} ORDER BY queue_seq DESC LIMIT ?", (*params, limit + 1)).fetchall()
    runs = [_row_to_run(row) for row in rows[:limit]]
    return {
        "status": "RUNS_OK",
        "runs": [summarize_run(run, cfg) for run in runs],
        "next_before_seq": runs[-1]["queue_seq"] if len(rows) > limit else None,
    }


# --- submission, queue and dispatch -----------------------------------------------


def _parse_prompt(prompt: str) -> tuple[list[dict[str, Any]], str, list[str]]:
    commands: list[dict[str, Any]] = []
    unknown: list[str] = []
    question_lines: list[str] = []
    for line in prompt.splitlines():
        stripped = line.strip()
        if stripped.startswith("/") and not stripped.startswith("//"):
            name, _, rest = stripped.partition(" ")
            spec = COMMANDS.get(name.lower())
            if spec is None:
                unknown.append(name)
                continue
            arguments: dict[str, Any] = dict(spec.get("extra", {}))
            if spec["arg"]:
                if not rest.strip():
                    unknown.append(f"{name} (missing {spec['arg']})")
                    continue
                arguments[spec["arg"]] = rest.strip()
            commands.append({"command": name.lower(), "tool": spec["tool"], "arguments": arguments})
        else:
            question_lines.append(line)
    return commands, "\n".join(question_lines).strip(), unknown


def submit_message(
    conversation_id: str,
    prompt: str,
    *,
    mode: str = "ask",
    options: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    interrupt: bool = False,
    dismiss_pending_question: bool = False,
    cfg: Settings = settings,
) -> dict[str, Any]:
    ensure_db(cfg.sqlite_path)
    text = (prompt or "").strip()
    if not text:
        raise RunError("ERROR_RUN_PROMPT_EMPTY", "Message text is required.")
    if len(text) > MAX_PROMPT_CHARS:
        raise RunError("ERROR_RUN_PROMPT_TOO_LONG", f"Message exceeds {MAX_PROMPT_CHARS} characters.")
    if mode not in MODES:
        raise RunError("ERROR_RUN_MODE_INVALID", f"Mode must be one of {sorted(MODES)}.")
    if get_conversation(conversation_id, cfg) is None:
        raise RunError("ERROR_CONVERSATION_NOT_FOUND", conversation_id)
    if dismiss_pending_question:
        pending = get_pending_interaction(conversation_id, cfg)
        if pending is not None:
            dismiss_interaction(pending["interaction_id"], expected_version=pending["version"], reason="NEW_MESSAGE_SUPERSEDES", dismissed_by="user", cfg=cfg)
            waiting_run = run_for_interaction(pending["interaction_id"], cfg)
            if waiting_run:
                resume_waiting_run(waiting_run, cfg)
    now = utc_now()
    with _Tx(cfg) as conn:
        if idempotency_key:
            row = conn.execute(
                "SELECT * FROM work_runs WHERE conversation_id = ? AND idempotency_key = ?", (conversation_id, idempotency_key)
            ).fetchone()
            if row is not None:
                existing = _row_to_run(row)
                return {"status": "RUN_DUPLICATE_RETURNED", "run": existing}
        seq_row = conn.execute("SELECT COALESCE(MAX(queue_seq), 0) AS seq FROM work_runs").fetchone()
        queue_seq = int(seq_row["seq"]) + 1
        run_id = stable_id("wrun", conversation_id, text, now, str(queue_seq))
        body: dict[str, Any] = {
            "run_id": run_id,
            "conversation_id": conversation_id,
            "prompt": text,
            "mode": mode,
            "options": dict(options or {}),
            "idempotency_key": idempotency_key,
            "created_at": now,
            "phase": "start",
            "tool_call_ids": [],
            "reported_call_ids": [],
            "approval_requested_ids": [],
            "round": 1,
            "cancel_requested": False,
        }
        conn.execute(
            "INSERT INTO work_runs (run_id, conversation_id, queue_seq, state, idempotency_key, version, payload_json) VALUES (?, ?, ?, 'QUEUED', ?, 1, ?)",
            (run_id, conversation_id, queue_seq, idempotency_key, json.dumps(body, ensure_ascii=False, sort_keys=True)),
        )
        position = conn.execute(
            "SELECT COUNT(*) AS n FROM work_runs WHERE conversation_id = ? AND state IN ('QUEUED','RUNNING','AWAITING_APPROVAL','AWAITING_INPUT') AND queue_seq < ?",
            (conversation_id, queue_seq),
        ).fetchone()["n"]
        _emit(conn, run_id, "queued", {"mode": mode, "ahead": int(position)})
        if interrupt:
            active = conn.execute(
                "SELECT * FROM work_runs WHERE conversation_id = ? AND state IN ('RUNNING','AWAITING_APPROVAL','AWAITING_INPUT')",
                (conversation_id,),
            ).fetchall()
            interrupt_ids = [row["run_id"] for row in active]
        else:
            interrupt_ids = []
    for active_id in interrupt_ids:
        stop_run(active_id, reason="SUPERSEDED_BY_NEW_MESSAGE", cfg=cfg)
    _dispatch(conversation_id, cfg)
    return {"status": "RUN_SUBMITTED", "run": get_run(run_id, cfg), "ahead": int(position)}


def _dispatch(conversation_id: str, cfg: Settings) -> str | None:
    """Claim the oldest queued run when the conversation has no active run (exactly-once)."""
    with _CONVERSATION_DISPATCH_LOCK, _Tx(cfg) as conn:
        active = conn.execute(
            "SELECT 1 FROM work_runs WHERE conversation_id = ? AND state IN ('RUNNING','AWAITING_APPROVAL','AWAITING_INPUT') LIMIT 1",
            (conversation_id,),
        ).fetchone()
        if active is not None:
            return None
        row = conn.execute(
            "SELECT * FROM work_runs WHERE conversation_id = ? AND state = 'QUEUED' ORDER BY queue_seq ASC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        if row is None:
            return None
        run = _row_to_run(row)
        run["state"] = "RUNNING"
        run["started_at"] = utc_now()
        _save(conn, run)
    _schedule(run["run_id"], cfg)
    return run["run_id"]


def _schedule(run_id: str, cfg: Settings) -> None:
    if _EXECUTION_MODE["mode"] == "inline":
        advance_run(run_id, cfg)
        return
    threading.Thread(target=advance_run, args=(run_id, cfg), name=f"run-{run_id}", daemon=True).start()


def edit_queued_run(run_id: str, prompt: str, *, expected_version: int, cfg: Settings = settings) -> dict[str, Any]:
    text = (prompt or "").strip()
    if not text:
        raise RunError("ERROR_RUN_PROMPT_EMPTY", "Message text is required.")
    with _Tx(cfg) as conn:
        run = _load(conn, run_id)
        if run["state"] != "QUEUED":
            raise RunError("ERROR_RUN_NOT_QUEUED", f"Only queued messages can be edited; run is {run['state']}.")
        if run["version"] != expected_version:
            raise RunError("ERROR_RUN_VERSION_CONFLICT", "The message changed since it was loaded.")
        run["prompt"] = text
        run["edited_at"] = utc_now()
        _save(conn, run)
    return get_run(run_id, cfg)


def stop_run(run_id: str, *, reason: str = "USER_STOP", cfg: Settings = settings) -> dict[str, Any]:
    """Cancel a queued run, or interrupt an active one before its next step (F03)."""
    ensure_db(cfg.sqlite_path)
    with _Tx(cfg) as conn:
        run = _load(conn, run_id)
        state = run["state"]
        if state in TERMINAL_STATES:
            raise RunError("ERROR_RUN_ALREADY_FINISHED", f"Run is {state}.")
        cancelled_before_start = state == "QUEUED"
        if cancelled_before_start:
            run["state"] = "CANCELLED"
            run["finished_at"] = utc_now()
            run["outcome"] = "CANCELLED_BEFORE_START"
            _save(conn, run)
            _emit(conn, run_id, "cancelled", {"reason": reason})
    if cancelled_before_start:
        return get_run(run_id, cfg)
    with _Tx(cfg) as conn:
        run = _load(conn, run_id)
        state = run["state"]
        if state in TERMINAL_STATES:
            raise RunError("ERROR_RUN_ALREADY_FINISHED", f"Run is {state}.")
        run["cancel_requested"] = True
        run["cancel_reason"] = reason
        run["stop_requested_at"] = utc_now()
        # Record what was executing when stop was pressed: its side effects may still land (F03).
        run["in_flight_at_stop"] = [call["tool_call_id"] for call in _run_calls(run, cfg) if call["status"] == "RUNNING"]
        _save(conn, run)
        waiting = state in {"AWAITING_APPROVAL", "AWAITING_INPUT"}
    if waiting:
        # Nothing is executing; finalize now under the run lock.
        _with_run_lock(run_id, lambda: _finalize_interrupt(run_id, cfg))
        _dispatch(run["conversation_id"], cfg)
    else:
        agent.abort(run_id)
    return get_run(run_id, cfg)


def _salvage_agent(run_id: str, reason: str, cfg: Settings) -> None:
    """Commit the finished part of an unfinished Claude turn so the conversation can continue.

    Never raises: stopping or failing a run must not depend on the history write succeeding.
    """
    run = get_run(run_id, cfg)
    state = run.get("agent")
    if not isinstance(state, dict) or state.get("committed") or not state.get("started"):
        return
    try:
        agent.salvage_run(run, SALVAGE_REASONS.get(reason, SALVAGE_REASONS["USER_STOP"]), cfg)
    except Exception:  # noqa: BLE001 - the next turn repairs open tool calls in the stored history
        return
    _update(run_id, cfg, lambda current: current.__setitem__("agent", run["agent"]))


def _finalize_interrupt(run_id: str, cfg: Settings) -> None:
    current = get_run(run_id, cfg)
    if current["state"] not in TERMINAL_STATES:
        _salvage_agent(run_id, current.get("cancel_reason") or "USER_STOP", cfg)
    calls = _run_calls(get_run(run_id, cfg), cfg)
    cancelled = []
    for call in calls:
        if call["status"] in {"PENDING_APPROVAL", "APPROVED"}:
            try:
                cancel_tool_call(call["tool_call_id"], cfg=cfg, cancelled_by="system", reason="run stopped")
                cancelled.append(call["tool_call_id"])
            except ToolRuntimeError:
                pass
    in_flight = [call["tool_call_id"] for call in calls if call["status"] == "RUNNING"]
    with _Tx(cfg) as conn:
        run = _load(conn, run_id)
        if run["state"] in TERMINAL_STATES:
            return
        pending_id = run.get("pending_interaction_id")
        run["state"] = "INTERRUPTED"
        run["finished_at"] = utc_now()
        run["outcome"] = run.get("cancel_reason") or "USER_STOP"
        _save(conn, run)
        _emit(conn, run_id, "interrupted", {
            "reason": run["outcome"],
            "cancelled_tool_call_ids": cancelled,
            "in_flight_at_stop": run.get("in_flight_at_stop", []),
            "still_running_tool_call_ids": in_flight,
        })
    if pending_id:
        interaction = get_interaction(pending_id, cfg)
        if interaction and interaction["status"] == "PENDING":
            try:
                dismiss_interaction(pending_id, expected_version=interaction["version"], reason="RUN_STOPPED", dismissed_by="system", cfg=cfg)
            except InteractionError:
                pass


def reconcile_runs_on_startup(cfg: Settings = settings) -> dict[str, Any]:
    """A RUNNING run at startup lost its executor: record INTERRUPTED; waiting runs stay resumable."""
    ensure_db(cfg.sqlite_path)
    with _Tx(cfg) as conn:
        rows = conn.execute("SELECT * FROM work_runs WHERE state = 'RUNNING'").fetchall()
        interrupted = []
        for row in rows:
            run = _row_to_run(row)
            run["state"] = "INTERRUPTED"
            run["finished_at"] = utc_now()
            run["outcome"] = "PROCESS_RESTART"
            _save(conn, run)
            _emit(conn, run["run_id"], "interrupted", {"reason": "PROCESS_RESTART"})
            interrupted.append(run["run_id"])
        conversations = [row["conversation_id"] for row in conn.execute("SELECT DISTINCT conversation_id FROM work_runs WHERE state = 'QUEUED'").fetchall()]
    # After the transaction: the history store takes its own write lock.
    for run_id in interrupted:
        _salvage_agent(run_id, "PROCESS_RESTART", cfg)
    for conversation_id in conversations:
        _dispatch(conversation_id, cfg)
    return {"status": "RUN_RECONCILE_OK", "interrupted": interrupted}


def resume_waiting_run(run_id: str, cfg: Settings = settings) -> None:
    """Called after a tool decision or question answer; advances the run if it can proceed."""
    run = get_run(run_id, cfg)
    if run["state"] in {"AWAITING_APPROVAL", "AWAITING_INPUT"}:
        _schedule(run_id, cfg)


def run_for_interaction(interaction_id: str, cfg: Settings = settings) -> str | None:
    ensure_db(cfg.sqlite_path)
    with get_connection(cfg.sqlite_path) as conn:
        rows = conn.execute("SELECT * FROM work_runs WHERE state = 'AWAITING_INPUT'").fetchall()
    for row in rows:
        if _row_to_run(row).get("pending_interaction_id") == interaction_id:
            return row["run_id"]
    return None


# --- step execution ---------------------------------------------------------------


def _with_run_lock(run_id: str, fn: Any) -> Any:
    with _LOCKS_GUARD:
        lock = _RUN_LOCKS.setdefault(run_id, threading.Lock())
    with lock:
        return fn()


def advance_run(run_id: str, cfg: Settings = settings) -> None:
    """Advance a run as far as it can go; exactly one advancer per run at a time."""
    with _LOCKS_GUARD:
        lock = _RUN_LOCKS.setdefault(run_id, threading.Lock())
    if not lock.acquire(blocking=False):
        with _LOCKS_GUARD:
            _RUN_KICKS.add(run_id)
        return
    try:
        while True:
            with _LOCKS_GUARD:
                _RUN_KICKS.discard(run_id)
            try:
                _advance_once(run_id, cfg)
            except Exception as exc:  # noqa: BLE001 - a crash must end the run FAILED, never silently RUNNING
                _fail(run_id, "ERROR_RUN_INTERNAL_FAILURE", f"{type(exc).__name__}: {exc}", cfg)
            with _LOCKS_GUARD:
                again = run_id in _RUN_KICKS
            if not again:
                break
    finally:
        lock.release()
    run = get_run(run_id, cfg)
    if run["state"] in TERMINAL_STATES:
        _dispatch(run["conversation_id"], cfg)


def _fail(run_id: str, status: str, detail: str, cfg: Settings) -> None:
    if get_run(run_id, cfg)["state"] not in TERMINAL_STATES:
        _salvage_agent(run_id, "FAILED", cfg)
    with _Tx(cfg) as conn:
        run = _load(conn, run_id)
        if run["state"] in TERMINAL_STATES:
            return
        run["state"] = "FAILED"
        run["finished_at"] = utc_now()
        run["error"] = {"status": status, "category": classify_error(status), "detail": detail}
        run["outcome"] = "FAILED"
        _save(conn, run)
        _emit(conn, run_id, "error", run["error"])
        _emit(conn, run_id, "final", {"outcome": "FAILED", "error": run["error"]})


def _update(run_id: str, cfg: Settings, change: Any, events: list[tuple[str, dict[str, Any]]] | None = None) -> dict[str, Any]:
    with _Tx(cfg) as conn:
        run = _load(conn, run_id)
        change(run)
        _save(conn, run)
        for event_type, data in events or []:
            _emit(conn, run_id, event_type, data)
    return run


def _emit_only(run_id: str, cfg: Settings, event_type: str, data: dict[str, Any]) -> None:
    with _Tx(cfg) as conn:
        _emit(conn, run_id, event_type, data)


def _cancel_checkpoint(run_id: str, cfg: Settings) -> bool:
    run = get_run(run_id, cfg)
    if run.get("cancel_requested") and run["state"] not in TERMINAL_STATES:
        _finalize_interrupt(run_id, cfg)
        return True
    return run["state"] in TERMINAL_STATES


def _advance_once(run_id: str, cfg: Settings) -> None:
    for _ in range(64):  # bounded phase transitions per advance
        if _cancel_checkpoint(run_id, cfg):
            return
        run = get_run(run_id, cfg)
        phase = run["phase"]
        if phase == "start":
            _phase_start(run, cfg)
        elif phase == "tools":
            if not _phase_tools(run, cfg):
                return
        elif phase == "tools_decision":
            if not _phase_tools_decision(run, cfg):
                return
        elif phase == "answer":
            _phase_answer(run, cfg)
        elif phase == "agent":
            if not _phase_agent(run, cfg):
                return
        elif phase == "done":
            return
        else:
            raise RunError("ERROR_RUN_PHASE_UNKNOWN", phase)


ENGINE_AGENT = "claude_agent"


class _AgentRoute(Exception):
    def __init__(self, model: str) -> None:
        super().__init__(model)
        self.model = model


def _agent_engine(run: dict[str, Any], question: str, cfg: Settings) -> bool:
    """Claude answers when it is connected and allowed; offline, local-model and plan runs keep the cited-answer path."""
    options = run.get("options", {})
    if run["mode"] == "plan" or not question:
        return False
    if options.get("require_offline") or options.get("model_provider") == "openai_compatible_local":
        return False
    return agent.agent_ready(cfg)


def _phase_agent(run: dict[str, Any], cfg: Settings) -> bool:
    """One Claude turn (``agent.advance_turn``). Returns True only if the run should keep advancing now."""
    run_id = run["run_id"]
    if run["state"] != "RUNNING":
        run = _update(run_id, cfg, lambda current: current.__setitem__("state", "RUNNING"))
    if not isinstance(run.get("agent"), dict):
        fresh = agent.new_turn_state(cfg)
        run = _update(run_id, cfg, lambda current: current.__setitem__("agent", fresh))

    def save(state: dict[str, Any]) -> None:
        _update(run_id, cfg, lambda current: current.__setitem__("agent", state))

    def should_stop() -> bool:
        current = get_run(run_id, cfg)
        return bool(current.get("cancel_requested")) or current["state"] in TERMINAL_STATES

    def register_calls(calls: list[dict[str, Any]]) -> None:
        ids = [call["tool_call_id"] for call in calls]
        _update(run_id, cfg, lambda current: current.__setitem__("tool_call_ids", current["tool_call_ids"] + ids),
                [("tool_call", _call_view(call)) for call in calls])

    hooks = agent.TurnHooks(
        emit=lambda event_type, data: _emit_only(run_id, cfg, event_type, data),
        save=save,
        should_stop=should_stop,
        register_calls=register_calls,
    )
    try:
        outcome = agent.advance_turn(run, cfg, hooks)
    finally:
        agent.release(run_id)
    if outcome.kind == "completed":
        _complete(run_id, cfg, outcome=outcome.outcome, assistant_text=agent.plain_answer(outcome.answer), agent_trace=outcome.trace,
                  action_ledger=[{"tool_call_id": call["tool_call_id"], "tool_name": call["tool_name"], "status": call["status"]}
                                 for call in _run_calls(get_run(run_id, cfg), cfg)])
        return False
    if outcome.kind == "awaiting_approval":
        current = get_run(run_id, cfg)
        calls = {call["tool_call_id"]: call for call in _run_calls(current, cfg)}
        new_ids = [call_id for call_id in outcome.awaiting_call_ids if call_id not in current["approval_requested_ids"] and call_id in calls]

        def wait(state: dict[str, Any]) -> None:
            state["state"] = "AWAITING_APPROVAL"
            state["approval_requested_ids"] = sorted(set(state["approval_requested_ids"]) | set(new_ids))

        _update(run_id, cfg, wait, [("approval_request", _call_view(calls[call_id])) for call_id in new_ids])
        return False
    if outcome.kind == "stopped":
        _finalize_interrupt(run_id, cfg)
        return False
    _fail(run_id, outcome.status or "ERROR_CLAUDE_REQUEST_FAILED", outcome.detail, cfg)
    return False


def _phase_start(run: dict[str, Any], cfg: Settings) -> None:
    run_id = run["run_id"]
    options = run.get("options", {})
    commands, question, unknown = _parse_prompt(run["prompt"])
    _emit_only(run_id, cfg, "run_started", {"mode": run["mode"], "prompt": run["prompt"]})
    use_agent = _agent_engine(run, question, cfg)
    try:
        if use_agent:
            model = agent.new_turn_state(cfg)["model"]
            raise _AgentRoute(model)
        route = plan_model_route(
            task_type="cited_qa",
            preferred_provider=options.get("model_provider"),
            allow_live=bool(options.get("allow_live")),
            require_offline=bool(options.get("require_offline")),
            cfg=cfg,
        )
        route_event = {
            "status": route["status"],
            "selected_provider": route["selected_provider"],
            "selected_model": route["selected_model"],
            "selection_reason": route["selection_reason"],
            "route_sha256": route["route_sha256"],
            "candidates": [{"provider": row["provider"], "eligible": row["eligible"], "reasons": row["reasons"]} for row in route["candidates"]],
        }
    except _AgentRoute as chosen:
        route_event = {"status": "CLAUDE_AGENT_READY", "selected_provider": "anthropic", "selected_model": chosen.model,
                       "selection_reason": "Claude is connected in Settings"}
    except (ModelRoutingError, ModelProviderError) as exc:
        route_event = {"status": "MODEL_ROUTE_BLOCKED", "error": getattr(exc, "status", type(exc).__name__)}
    _emit_only(run_id, cfg, "route", route_event)
    for name in unknown:
        _emit_only(run_id, cfg, "warning", {"status": "WARN_UNKNOWN_COMMAND", "category": "INPUT_INVALID", "detail": name})
    steps = [
        {"step": f"{command['command']} -> {command['tool']}", "tool": command["tool"], "arguments": command["arguments"],
         "requires_approval": not (run["mode"] in AUTO_APPROVE_MODES and _auto_approvable(command["tool"]))}
        for command in commands
    ]
    if question and use_agent:
        steps += [{"step": "answer with Claude, using tools as needed"}]
    elif question:
        steps += [{"step": "retrieve local sources"}, {"step": "compose a cited answer"}]
    _emit_only(run_id, cfg, "plan", {"goal": question or "Run the requested actions.", "mode": run["mode"], "steps": steps})

    if run["mode"] == "plan":
        summary = "Plan only; nothing was executed. Steps: " + "; ".join(step["step"] for step in steps) if steps else "Plan only; nothing to do."
        _complete(run_id, cfg, outcome="PLAN_ONLY", assistant_text=summary)
        return

    call_ids = []
    events: list[tuple[str, dict[str, Any]]] = []
    for command in commands:
        try:
            call = request_tool_call(command["tool"], command["arguments"], cfg=cfg, requested_by="user", reason=command["command"], run_id=run_id)["tool_call"]
        except ToolRuntimeError as exc:
            events.append(("warning", {"status": exc.status, "category": classify_error(exc.status), "detail": exc.detail}))
            continue
        if run["mode"] in AUTO_APPROVE_MODES and _auto_approvable(call["tool_name"]):
            call = approve_tool_call(call["tool_call_id"], cfg=cfg, approved_by="policy:auto-local-read-only", expected_arguments_sha256=call["arguments_sha256"])["tool_call"]
        call_ids.append(call["tool_call_id"])
        events.append(("tool_call", _call_view(call)))

    def change(current: dict[str, Any]) -> None:
        current["tool_call_ids"] = call_ids
        current["question"] = question
        if use_agent:
            current["engine"] = ENGINE_AGENT
        current["phase"] = "tools" if call_ids else ("agent" if use_agent and question else "answer")

    _update(run_id, cfg, change, events)


def _call_view(call: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool_call_id": call["tool_call_id"],
        "tool_name": call["tool_name"],
        "arguments": call.get("arguments", {}),
        "arguments_sha256": call.get("arguments_sha256"),
        "status": call["status"],
        "retry_of": call.get("retry_of"),
    }


def _result_view(call: dict[str, Any]) -> dict[str, Any]:
    result = call.get("result") or {}
    status = call["status"]
    view = {**_call_view(call), "result_status": result.get("status")}
    if status in {"FAILED", "INTERRUPTED"}:
        view["error"] = {"status": result.get("status"), "category": classify_error(result.get("status")), "detail": result.get("detail")}
    elif status in {"DENIED", "CANCELLED"}:
        view["error"] = {"status": f"TOOL_CALL_{status}", "category": "BLOCKED_ACTION", "detail": None}
    else:
        view["result"] = _compact(result)
    return view


def _compact(value: Any, depth: int = 0) -> Any:
    """Bound result payloads in events; the full result stays on the tool call record."""
    if depth > 3:
        return "…"
    if isinstance(value, dict):
        return {key: _compact(item, depth + 1) for key, item in list(value.items())[:40]}
    if isinstance(value, list):
        return [_compact(item, depth + 1) for item in value[:20]]
    if isinstance(value, str) and len(value) > 2000:
        return value[:2000] + "…"
    return value


def _phase_tools(run: dict[str, Any], cfg: Settings) -> bool:
    run_id = run["run_id"]
    calls = _run_calls(run, cfg)
    pending = [call for call in calls if call["status"] == "PENDING_APPROVAL"]
    if pending:
        new_requests = [call for call in pending if call["tool_call_id"] not in run["approval_requested_ids"]]

        def change(current: dict[str, Any]) -> None:
            current["state"] = "AWAITING_APPROVAL"
            current["approval_requested_ids"] = sorted(set(current["approval_requested_ids"]) | {call["tool_call_id"] for call in new_requests})

        _update(run_id, cfg, change, [("approval_request", _call_view(call)) for call in new_requests])
        return False
    if run["state"] != "RUNNING":
        _update(run_id, cfg, lambda current: current.__setitem__("state", "RUNNING"))
    for call in calls:
        if call["status"] != "APPROVED":
            continue
        if _cancel_checkpoint(run_id, cfg):
            return False
        _emit_only(run_id, cfg, "tool_started", _call_view(call))
        try:
            run_tool_call(call["tool_call_id"], cfg=cfg, executed_by=f"run:{run_id}")
        except ToolRuntimeError:
            pass  # the failure is recorded on the call; reported below from the record
    calls = _run_calls(get_run(run_id, cfg), cfg)
    reported = set(get_run(run_id, cfg)["reported_call_ids"])
    terminal = [call for call in calls if call["status"] in {"EXECUTED", "FAILED", "DENIED", "CANCELLED", "INTERRUPTED"}]
    unreported = [call for call in terminal if call["tool_call_id"] not in reported]
    if len(terminal) != len(calls):
        return False  # something is still running elsewhere; a later kick resumes
    round_ids = set(get_run(run_id, cfg).get("round_call_ids") or [call["tool_call_id"] for call in calls])
    round_calls = [call for call in calls if call["tool_call_id"] in round_ids]
    failed = [call for call in round_calls if call["status"] in {"FAILED", "INTERRUPTED"}]
    events = [("tool_result", _result_view(call)) for call in unreported]
    events.append(("tool_summary", {"round": run["round"], **summarize_tool_calls(calls)}))

    def record_round(current: dict[str, Any]) -> None:
        current["reported_call_ids"] = sorted(set(current["reported_call_ids"]) | {call["tool_call_id"] for call in unreported})
        current["phase"] = "tools_decision" if failed else "answer"
        current["failed_call_ids"] = [call["tool_call_id"] for call in failed]

    _update(run_id, cfg, record_round, events)
    if failed:
        _ask_about_failures(get_run(run_id, cfg), failed, len(round_calls), cfg)
        return False
    return True


def _ask_about_failures(run: dict[str, Any], failed: list[dict[str, Any]], total: int, cfg: Settings) -> None:
    names = ", ".join(sorted({call["tool_name"] for call in failed}))
    interaction = create_interaction(
        run["conversation_id"],
        question=f"{len(failed)} of {total} actions failed ({names}). How should Mucaro continue?",
        options=[
            {"option_id": "retry_failed", "label": "Retry the failed action" + ("s" if len(failed) > 1 else ""),
             "consequence": "Requests only the failed action(s) again; each needs a fresh approval. Successful actions are not repeated."},
            {"option_id": "continue", "label": "Continue without them", "consequence": "Uses only the results that succeeded."},
            {"option_id": "stop", "label": "Stop here", "consequence": "Ends this run; nothing else is executed."},
        ],
        allow_freeform=True,
        dismiss_pending=True,
        created_by="run",
        cfg=cfg,
    )

    def change(current: dict[str, Any]) -> None:
        current["state"] = "AWAITING_INPUT"
        current["pending_interaction_id"] = interaction["interaction_id"]

    _update(run["run_id"], cfg, change, [("question", {"interaction_id": interaction["interaction_id"], "version": interaction["version"],
                                                       "question": interaction["question"], "options": interaction["options"],
                                                       "allow_freeform": interaction["allow_freeform"]})])


def _phase_tools_decision(run: dict[str, Any], cfg: Settings) -> bool:
    run_id = run["run_id"]
    interaction = get_interaction(run.get("pending_interaction_id") or "", cfg)
    if interaction is None or interaction["status"] == "PENDING":
        if run["state"] != "AWAITING_INPUT":
            _update(run_id, cfg, lambda current: current.__setitem__("state", "AWAITING_INPUT"))
        return False
    resolution = interaction.get("resolution") or {}
    choice = resolution.get("option_id")
    resolved_event = {"interaction_id": interaction["interaction_id"], "status": interaction["status"], "option_id": choice,
                      "freeform_text": resolution.get("freeform_text")}
    if interaction["status"] == "DISMISSED":
        _emit_only(run_id, cfg, "question_resolved", resolved_event)
        _complete(run_id, cfg, outcome="QUESTION_DISMISSED", assistant_text="Stopped: the question was dismissed, so no further actions ran.")
        return False
    if choice == "stop":
        _emit_only(run_id, cfg, "question_resolved", resolved_event)
        _complete(run_id, cfg, outcome="STOPPED_BY_USER_CHOICE", assistant_text="Stopped at your request; no further actions ran.")
        return False
    if choice == "retry_failed":
        failed = [call for call in _run_calls(run, cfg) if call["tool_call_id"] in set(run.get("failed_call_ids", []))]
        new_ids, events = [], [("question_resolved", resolved_event)]
        for call in failed:
            retry = request_tool_call(call["tool_name"], dict(call.get("arguments") or {}), cfg=cfg, requested_by="user",
                                      reason=f"retry of {call['tool_call_id']}", run_id=run_id, retry_of=call["tool_call_id"])["tool_call"]
            if run["mode"] in AUTO_APPROVE_MODES and _auto_approvable(retry["tool_name"]):
                retry = approve_tool_call(retry["tool_call_id"], cfg=cfg, approved_by="policy:auto-local-read-only", expected_arguments_sha256=retry["arguments_sha256"])["tool_call"]
            new_ids.append(retry["tool_call_id"])
            events.append(("retry_requested", _call_view(retry)))

        def change(current: dict[str, Any]) -> None:
            current["tool_call_ids"] = current["tool_call_ids"] + new_ids
            current["round_call_ids"] = new_ids
            current["round"] = current["round"] + 1
            current["pending_interaction_id"] = None
            current["state"] = "RUNNING"
            current["phase"] = "tools"

        _update(run_id, cfg, change, events)
        return True
    events = [("question_resolved", resolved_event)]
    if resolution.get("freeform_text"):
        events.append(("user_note", {"text": resolution["freeform_text"]}))

    def proceed(current: dict[str, Any]) -> None:
        current["pending_interaction_id"] = None
        current["state"] = "RUNNING"
        current["phase"] = "answer"

    _update(run_id, cfg, proceed, events)
    return True


def _phase_answer(run: dict[str, Any], cfg: Settings) -> None:
    run_id = run["run_id"]
    question = run.get("question") or ""
    calls = _run_calls(run, cfg)
    ledger = [{"tool_call_id": call["tool_call_id"], "tool_name": call["tool_name"], "status": call["status"]} for call in calls]
    if not question:
        summary = summarize_tool_calls(calls)
        text = f"Completed {summary['by_status'].get('EXECUTED', 0)} of {summary['total']} actions."
        _complete(run_id, cfg, outcome="ACTIONS_COMPLETED", assistant_text=text, action_ledger=ledger)
        return
    if run.get("engine") == ENGINE_AGENT:
        _update(run_id, cfg, lambda current: current.__setitem__("phase", "agent"))
        return
    options = run.get("options", {})
    conversation = get_conversation(run["conversation_id"], cfg) or {"turns": []}

    def stream_delta(text: str) -> None:
        # Live token streaming from providers that support it (TGT-002 STREAM RESULTS).
        _emit_only(run_id, cfg, "text_delta", {"text": text, "live": True})

    try:
        trace = answer_question(
            question,
            top_k=int(options.get("top_k", 5)),
            retrieval_strategy=str(options.get("retrieval_strategy") or "hybrid"),
            model_provider=options.get("model_provider"),
            model_name=options.get("model_name"),
            allow_mock_fallback=bool(options.get("allow_mock_fallback")),
            allow_live=bool(options.get("allow_live")),
            require_offline=bool(options.get("require_offline")),
            conversation_context=build_conversation_context(conversation),
            cfg=cfg,
            on_text_delta=stream_delta,
        )
    except (ModelProviderError, ModelRoutingError, ValueError) as exc:
        status = getattr(exc, "status", None) or str(exc)
        _fail(run_id, status, getattr(exc, "detail", str(exc)), cfg)
        return
    if "answer" not in trace:
        status = str(trace.get("status") or "ERROR_ANSWER_UNAVAILABLE")
        _fail(run_id, status, str(trace.get("detail") or trace.get("warnings") or status), cfg)
        return
    events: list[tuple[str, dict[str, Any]]] = [
        ("retrieval", {"strategy": trace.get("retrieval_strategy"), "retrieved_chunk_ids": trace.get("retrieved_chunk_ids", []),
                       "source_ids": trace.get("source_ids", [])}),
    ]
    answer = str(trace["answer"])
    if not trace.get("answer_streamed"):
        for start in range(0, len(answer), TEXT_DELTA_CHARS):
            events.append(("text_delta", {"text": answer[start:start + TEXT_DELTA_CHARS], "live": False}))
    for record in trace.get("cited_chunks", []):
        events.append(("citation", {key: record.get(key) for key in (
            "citation_order", "retrieval_rank", "chunk_id", "source_id", "text_snippet", "score", "source_format",
            "source_url", "snapshot_id", "retrieval_utc", "trust")}))
    for kind, path in sorted((trace.get("export_paths") or {}).items()):
        events.append(("artifact", {"artifact_id": stable_id("art", trace["run_id"], kind), "artifact_type": "EXPORT",
                                    "logical_identity": f"answer_trace:{trace['run_id']}:{kind}", "source_path_or_uri": path}))
    for flag in trace.get("validation_flags", []):
        if str(flag).startswith("ERROR_") or str(flag).startswith("WARN"):
            events.append(("warning", {"status": flag, "category": classify_error(flag)}))
    with _Tx(cfg) as conn:
        for event_type, data in events:
            _emit(conn, run_id, event_type, data)
    _complete(run_id, cfg, outcome="ANSWERED", answer_trace=trace, action_ledger=ledger)


def _complete(
    run_id: str,
    cfg: Settings,
    *,
    outcome: str,
    assistant_text: str | None = None,
    answer_trace: dict[str, Any] | None = None,
    action_ledger: list[dict[str, Any]] | None = None,
    agent_trace: dict[str, Any] | None = None,
) -> None:
    run = get_run(run_id, cfg)
    now = utc_now()
    user_turn = {"turn_id": stable_id("turn", run["conversation_id"], "user", run_id, length=12), "role": "user",
                 "content": run["prompt"], "created_at": run["created_at"], "status": "USER_MESSAGE", "work_run_id": run_id}
    if answer_trace is not None:
        assistant_turn = {
            "turn_id": stable_id("turn", run["conversation_id"], "assistant", run_id, length=12),
            "role": "assistant",
            "content": answer_trace.get("answer", ""),
            "created_at": now,
            "status": answer_trace.get("citation_status") or "DONE",
            "run_id": answer_trace.get("run_id"),
            "work_run_id": run_id,
            "retrieval_strategy": answer_trace.get("retrieval_strategy"),
            "cited_chunk_ids": answer_trace.get("cited_chunk_ids", []),
            "source_ids": answer_trace.get("source_ids", []),
            "validation_flags": answer_trace.get("validation_flags", []),
            "answer_trace": answer_trace,
        }
    else:
        assistant_turn = {"turn_id": stable_id("turn", run["conversation_id"], "assistant", run_id, length=12), "role": "assistant",
                          "content": assistant_text or "", "created_at": now, "status": outcome, "work_run_id": run_id}
        if agent_trace is not None:
            assistant_turn["agent_trace"] = agent_trace
    append_turns(run["conversation_id"], [user_turn, assistant_turn], cfg, title_hint=run["prompt"])
    final = {"outcome": outcome, "action_ledger": action_ledger or [], "citation_status": (answer_trace or {}).get("citation_status"),
             "answer_run_id": (answer_trace or {}).get("run_id"), "assistant_turn_id": assistant_turn["turn_id"]}
    if agent_trace is not None:
        final["model"] = agent_trace.get("model")

    def change(current: dict[str, Any]) -> None:
        current["state"] = "COMPLETED"
        current["phase"] = "done"
        current["outcome"] = outcome
        current["finished_at"] = now
        current["answer_run_id"] = final["answer_run_id"]

    _update(run_id, cfg, change, [("final", final)])


# --- projections --------------------------------------------------------------------


def run_envelope(run_id: str, cfg: Settings = settings) -> dict[str, Any]:
    """Typed envelope for one run; tool parts come from call records, not narration (F08, F15)."""
    run = get_run(run_id, cfg)
    events = list_run_events(run_id, cfg=cfg, limit=5000)
    parts: list[dict[str, Any]] = [make_text_part(run["prompt"])]
    for call in _run_calls(run, cfg):
        parts.append({**make_tool_call_part(call), "arguments_sha256": call.get("arguments_sha256"), "retry_of": call.get("retry_of")})
        if call["status"] in {"EXECUTED", "FAILED", "DENIED", "CANCELLED", "INTERRUPTED"}:
            parts.append({**make_tool_result_part(call), "error_category": classify_error((call.get("result") or {}).get("status"))
                          if call["status"] != "EXECUTED" else None})
    if run.get("pending_interaction_id"):
        interaction = get_interaction(run["pending_interaction_id"], cfg)
        if interaction is not None:
            parts.append(make_question_part(interaction))
    narrated = {event["data"].get("segment") for event in events if event["type"] == "progress" and event["data"].get("kind") == "narration"}
    answer = agent.plain_answer("".join(
        event["data"].get("text", "") for event in events
        if event["type"] == "text_delta" and event["data"].get("segment") not in narrated
    ))
    if answer:
        parts.append(make_text_part(answer))
    for event in events:
        if event["type"] == "artifact":
            parts.append(make_artifact_part(event["data"]))
    parts.append(make_task_receipt_part({"task_id": run_id, "status": run["state"], "summary": summarize_run(run, cfg)}))
    return build_response_envelope(run_id, parts)


def stream_run_events(
    run_id: str,
    after_seq: int,
    cfg: Settings = settings,
    *,
    poll_seconds: float = 0.25,
    heartbeat_seconds: float = 10.0,
    max_seconds: float = 1800.0,
) -> Iterator[str]:
    """Server-sent events: replays from after_seq, then follows until the run is terminal."""
    last = after_seq
    started = time.monotonic()
    last_beat = started
    while True:
        generation = _COMMIT_GENERATION[0]
        # Read state before events: a run already terminal at this read has committed every event,
        # so ending on an empty read can never drop events written between the two reads.
        run = get_run(run_id, cfg)
        events = list_run_events(run_id, after_seq=last, cfg=cfg)
        for event in events:
            last = event["seq"]
            yield f"id: {event['seq']}\nevent: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        if run["state"] in TERMINAL_STATES and not events:
            yield f"event: end\ndata: {json.dumps({'run_id': run_id, 'state': run['state'], 'last_seq': last})}\n\n"
            return
        now = time.monotonic()
        if now - started > max_seconds:
            yield f"event: timeout\ndata: {json.dumps({'run_id': run_id, 'last_seq': last})}\n\n"
            return
        if now - last_beat > heartbeat_seconds:
            last_beat = now
            yield ": keep-alive\n\n"
        if not events:
            with _EVENTS_COMMITTED:
                _EVENTS_COMMITTED.wait_for(lambda seen=generation: _COMMIT_GENERATION[0] != seen, timeout=poll_seconds)
