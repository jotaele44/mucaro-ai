"""MATRIX-016 recovery benchmark: every case of the frozen recovery denominator, run against the real recovery code.

Each case runs in its own temporary workspace with the mock model and no network, through the same functions the
API and the work runs use. Restart cases use separate Python processes on one data directory; the app-restart case
kills its first process in the middle of a tool call. Every case checks both the recovered state and that nothing
ran twice. The receipt records each case's observed outcome, closes its arithmetic, and binds the manifest bytes.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .agent_checkpoints import (
    CheckpointError,
    begin_step,
    complete_step,
    create_checkpoint,
    get_checkpoint,
    resume_checkpoint,
    transition_checkpoint,
)
from .config import Settings
from .conversations import create_conversation
from .memory_benchmark import _sha256_bytes, _stable_hash
from .model_router import MODEL_PROVIDER_SPECS, ModelProviderError
from .provider_recovery import execute_with_provider_checkpoint, resume_with_provider
from .tools import TOOL_REGISTRY, ToolDefinition, ToolRuntimeError, list_tool_calls
from .utils import utc_now

MANIFEST_PATH = Path("_architecture") / "recovery_benchmark_v1.json"
MANIFEST_SCHEMA_VERSION = "mucaro-matrix016-recovery-benchmark/1.0"
BENCHMARK_SCHEMA_VERSION = "mucaro-matrix016-recovery-cert/1.0"
STATUS_OK = "MATRIX016_RECOVERY_CERT_OK"
STATUS_FAILED = "MATRIX016_RECOVERY_CERT_FAILED"
REQUIRED_CATEGORIES = (
    "app_restart",
    "workflow_restart",
    "resume",
    "cancel",
    "crash_recovery",
    "partial_tool_failure",
    "network_interruption",
    "offline_transition",
)
SHA = "d" * 40
CLOSED_LOOPBACK_URL = "http://127.0.0.1:9/v1"  # discard port: nothing listens, so the connection is refused


def _settings(root: Path) -> Settings:
    data = root / "data"
    cfg = Settings(
        project_root=root,
        data_dir=data,
        input_dir=data / "input",
        processed_dir=data / "processed",
        export_dir=data / "exports",
        index_dir=data / "indexes",
        sqlite_path=data / "processed" / "matrix016.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )
    cfg.ensure_dirs()
    return cfg


@contextmanager
def _inline_runs() -> Iterator[Any]:
    from . import work_runs  # the work-run engine is only needed by the run cases

    previous = work_runs._EXECUTION_MODE["mode"]
    work_runs.set_execution_mode("inline")
    try:
        yield work_runs
    finally:
        work_runs.set_execution_mode(previous)


@contextmanager
def _environment(**values: str) -> Iterator[None]:
    from . import model_router

    saved = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    model_router._PROBE_CACHE.clear()
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        model_router._PROBE_CACHE.clear()


@contextmanager
def _flaky_tool(work_runs: Any) -> Iterator[dict[str, int]]:
    """A local read-only tool that fails on its first execution and succeeds afterwards."""
    calls = {"n": 0}

    def handler(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ToolRuntimeError("ERROR_TOOL_BENCHMARK_TRANSIENT", "first attempt fails")
        return {"status": "FLAKY_OK", "attempt": calls["n"]}

    TOOL_REGISTRY["benchmark.flaky"] = ToolDefinition("benchmark.flaky", "fails once", False, True, handler)
    work_runs.COMMANDS["/flaky"] = {"tool": "benchmark.flaky", "usage": "/flaky", "arg": None}
    try:
        yield calls
    finally:
        TOOL_REGISTRY.pop("benchmark.flaky", None)
        work_runs.COMMANDS.pop("/flaky", None)


def _run_python(project_root: Path, code: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(project_root), str(project_root / "backend")])}
    return subprocess.run(
        [sys.executable, "-c", code, *args],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _calls_by_tool(cfg: Settings, run_id: str | None = None) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for call in list_tool_calls(cfg)["tool_calls"]:
        if run_id is None or call.get("run_id") == run_id:
            grouped.setdefault(call["tool_name"], []).append(call)
    return grouped


def _last_json(proc: Any) -> dict[str, Any]:
    return json.loads(proc.stdout.strip().splitlines()[-1])


# --- app_restart ------------------------------------------------------------------------------------------------

_DIES_MID_TOOL = """
import json, os, sys
from pathlib import Path
from backend.app import work_runs
from backend.app.conversations import create_conversation
from backend.app.recovery_benchmark import _settings
from backend.app.tools import TOOL_REGISTRY, ToolDefinition

def dies(arguments, cfg):
    os._exit(17)

TOOL_REGISTRY["benchmark.dies"] = ToolDefinition("benchmark.dies", "process dies mid-call", False, True, dies)
work_runs.COMMANDS["/dies"] = {"tool": "benchmark.dies", "usage": "/dies", "arg": None}
work_runs.set_execution_mode("inline")
cfg = _settings(Path(sys.argv[1]))
chat = create_conversation("restart", cfg)["conversation_id"]
work_runs.submit_message(chat, "/dies", mode="auto", cfg=cfg)
"""

_RESTARTED_APP = """
import json, sys
from pathlib import Path
from backend.app.main import create_app
from backend.app.recovery_benchmark import _settings
from backend.app.tools import ToolRuntimeError, list_tool_audit_events, list_tool_calls, run_tool_call
from backend.app.work_runs import get_run, list_run_events
cfg = _settings(Path(sys.argv[1]))
stored = list_tool_calls(cfg)["tool_calls"]
run_id = stored[0]["run_id"]
before = [call["status"] for call in stored]
run_before = get_run(run_id, cfg)["state"]
create_app(cfg)
calls = list_tool_calls(cfg)["tool_calls"]
try:
    run_tool_call(calls[0]["tool_call_id"], cfg=cfg)
    rerun = "RERUN_ALLOWED"
except ToolRuntimeError as exc:
    rerun = exc.status
run = get_run(run_id, cfg)
print(json.dumps({
    "calls_before": before,
    "run_before": run_before,
    "calls_after": [call["status"] for call in calls],
    "run_after": run["state"],
    "run_outcome": run["outcome"],
    "rerun": rerun,
    "tool_interrupted_events": sum(1 for e in list_tool_audit_events(cfg)["events"] if e.get("event") == "TOOL_CALL_INTERRUPTED"),
    "run_interrupted_events": sum(1 for e in list_run_events(run_id, cfg=cfg) if e["type"] == "interrupted"),
}))
"""


def _app_restart_reconciles(_cfg: Settings, root: Path, *, project_root: Path) -> tuple[str, dict[str, Any]]:
    workspace = root / "app"
    workspace.mkdir(parents=True, exist_ok=True)
    first = _run_python(project_root, _DIES_MID_TOOL, workspace)
    if first.returncode != 17:
        return "FIRST_PROCESS_DID_NOT_DIE_MID_CALL", {"returncode": first.returncode, "stderr": first.stderr[-400:]}
    second = _run_python(project_root, _RESTARTED_APP, str(workspace))
    if second.returncode != 0:
        return "RESTARTED_APP_FAILED", {"stderr": second.stderr[-400:]}
    seen = _last_json(second)
    ok = seen == {
        "calls_before": ["RUNNING"],
        "run_before": "RUNNING",
        "calls_after": ["INTERRUPTED"],
        "run_after": "INTERRUPTED",
        "run_outcome": "PROCESS_RESTART",
        "rerun": "ERROR_TOOL_CALL_NOT_APPROVED",
        "tool_interrupted_events": 1,
        "run_interrupted_events": 1,
    }
    return ("RUNNING_MARKED_INTERRUPTED_NOT_RERUN" if ok else "RESTART_STATE_WRONG"), seen


# --- workflow_restart -------------------------------------------------------------------------------------------

_CHECKPOINT_WRITER = f"""
import json, sys
from pathlib import Path
from backend.app.agent_checkpoints import create_checkpoint, transition_checkpoint
from backend.app.recovery_benchmark import _settings
cfg = _settings(Path(sys.argv[1]))
created = create_checkpoint(
    session_id="s-restart", run_id="r-restart", objective="Resume exact state", source_sha="{SHA}", state="EXECUTING",
    completed_steps=[{{"id": "done-1", "status": "PASS"}}], pending_steps=[{{"id": "next-1", "status": "OPEN"}}],
    provider="mock", model="mock", cfg=cfg,
)
print(json.dumps(transition_checkpoint(created["checkpoint_id"], state="PAUSED_PROVIDER", pause_reason="restart", cfg=cfg)))
"""

_CHECKPOINT_READER = """
import json, sys
from pathlib import Path
from backend.app.agent_checkpoints import CheckpointError, get_checkpoint, resume_checkpoint
from backend.app.recovery_benchmark import _settings
cfg = _settings(Path(sys.argv[1]))
checkpoint_id = sys.argv[2]
before = get_checkpoint(checkpoint_id, cfg)
resumed = resume_checkpoint(checkpoint_id, cfg)
try:
    resume_checkpoint(checkpoint_id, cfg)
    second = "RESUMED_TWICE"
except CheckpointError as exc:
    second = exc.status
print(json.dumps({"before": before, "resumed": resumed, "second_resume": second, "stored": get_checkpoint(checkpoint_id, cfg)}))
"""


def _workflow_checkpoint_survives_process(_cfg: Settings, root: Path, *, project_root: Path) -> tuple[str, dict[str, Any]]:
    workspace = root / "checkpoint"
    workspace.mkdir(parents=True, exist_ok=True)
    writer = _run_python(project_root, _CHECKPOINT_WRITER, str(workspace))
    if writer.returncode != 0:
        return "WRITER_FAILED", {"stderr": writer.stderr[-400:]}
    paused = _last_json(writer)
    reader = _run_python(project_root, _CHECKPOINT_READER, str(workspace), paused["checkpoint_id"])
    if reader.returncode != 0:
        return "READER_FAILED", {"stderr": reader.stderr[-400:]}
    seen = _last_json(reader)
    before, resumed = seen["before"], seen["resumed"]
    identity = ("checkpoint_id", "session_id", "run_id", "source_sha", "objective", "completed_steps", "pending_steps")
    ok = (
        before == paused
        and all(resumed[field] == paused[field] for field in identity)
        and resumed["state"] == "EXECUTING"
        and resumed["sequence"] == paused["sequence"] + 1
        and seen["second_resume"] != "RESUMED_TWICE"
        and seen["stored"] == resumed
    )
    return ("IDENTICAL_STATE_SEQUENCE_PLUS_ONE" if ok else "STATE_CHANGED_OR_DUPLICATED"), {
        "sequence_before": paused["sequence"],
        "sequence_after": resumed["sequence"],
        "second_resume": seen["second_resume"],
    }


def _corrupt_checkpoint_fails_closed(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    schema_bad = create_checkpoint(session_id="s", run_id="r1", objective="bad state", source_sha=SHA, state="EXECUTING", cfg=cfg)
    unreadable = create_checkpoint(session_id="s", run_id="r2", objective="bad bytes", source_sha=SHA, state="EXECUTING", cfg=cfg)
    with sqlite3.connect(cfg.sqlite_path) as conn:
        conn.execute(
            "UPDATE agent_checkpoints SET payload_json = ? WHERE checkpoint_id = ?",
            (json.dumps({**schema_bad, "state": "NOT_A_STATE"}), schema_bad["checkpoint_id"]),
        )
        conn.execute("UPDATE agent_checkpoints SET payload_json = ? WHERE checkpoint_id = ?", ("{not-json", unreadable["checkpoint_id"]))
    outcomes = []
    for checkpoint_id in (schema_bad["checkpoint_id"], unreadable["checkpoint_id"]):
        try:
            loaded = get_checkpoint(checkpoint_id, cfg)
            outcomes.append(f"LOADED:{(loaded or {}).get('state')}")
        except CheckpointError as exc:
            outcomes.append(exc.status)
    ok = outcomes == ["ERROR_CHECKPOINT_SCHEMA", "ERROR_CHECKPOINT_CORRUPT"]
    return ("CHECKPOINT_REJECTED" if ok else "CORRUPT_CHECKPOINT_LOADED"), {"outcomes": outcomes}


# --- resume -----------------------------------------------------------------------------------------------------

_WAITING_WRITER = """
import json, sys
from pathlib import Path
from backend.app import work_runs
from backend.app.conversations import create_conversation
from backend.app.recovery_benchmark import _settings
work_runs.set_execution_mode("inline")
cfg = _settings(Path(sys.argv[1]))
chat = create_conversation("resume", cfg)["conversation_id"]
run = work_runs.submit_message(chat, "/status", mode="ask", cfg=cfg)["run"]
print(json.dumps({"run_id": run["run_id"], "state": work_runs.get_run(run["run_id"], cfg)["state"]}))
"""

_WAITING_RESUMER = """
import json, sys
from pathlib import Path
from backend.app import work_runs
from backend.app.main import create_app
from backend.app.recovery_benchmark import _settings
from backend.app.tools import approve_tool_call, list_tool_calls
work_runs.set_execution_mode("inline")
cfg = _settings(Path(sys.argv[1]))
run_id = sys.argv[2]
create_app(cfg)
after_restart = work_runs.get_run(run_id, cfg)["state"]
approve_tool_call(work_runs.get_run(run_id, cfg)["tool_call_ids"][0], cfg=cfg)
work_runs.resume_waiting_run(run_id, cfg)
work_runs.resume_waiting_run(run_id, cfg)
run = work_runs.get_run(run_id, cfg)
calls = list_tool_calls(cfg)["tool_calls"]
print(json.dumps({
    "after_restart": after_restart,
    "final": run["state"],
    "call_statuses": [call["status"] for call in calls],
    "finals": sum(1 for e in work_runs.list_run_events(run_id, cfg=cfg) if e["type"] == "final"),
    "runs": len(work_runs.list_runs(cfg=cfg)["runs"]),
}))
"""


def _resume_waiting_after_restart(_cfg: Settings, root: Path, *, project_root: Path) -> tuple[str, dict[str, Any]]:
    workspace = root / "resume"
    workspace.mkdir(parents=True, exist_ok=True)
    writer = _run_python(project_root, _WAITING_WRITER, str(workspace))
    if writer.returncode != 0:
        return "WRITER_FAILED", {"stderr": writer.stderr[-400:]}
    waiting = _last_json(writer)
    resumer = _run_python(project_root, _WAITING_RESUMER, str(workspace), waiting["run_id"])
    if resumer.returncode != 0:
        return "RESUMER_FAILED", {"stderr": resumer.stderr[-400:]}
    seen = _last_json(resumer)
    ok = waiting["state"] == "AWAITING_APPROVAL" and seen == {
        "after_restart": "AWAITING_APPROVAL",
        "final": "COMPLETED",
        "call_statuses": ["EXECUTED"],
        "finals": 1,
        "runs": 1,
    }
    return ("COMPLETED_ONCE" if ok else "RESUME_LOST_OR_DUPLICATED"), seen


# --- cancel -----------------------------------------------------------------------------------------------------


def _cancel_awaiting_run(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    with _inline_runs() as work_runs:
        chat = create_conversation("cancel", cfg)["conversation_id"]
        run = work_runs.submit_message(chat, "/status\n/memory", cfg=cfg)["run"]
        queued = work_runs.submit_message(chat, "What is in the library?", cfg=cfg)["run"]
        stopped = work_runs.stop_run(run["run_id"], cfg=cfg)
        try:
            work_runs.stop_run(run["run_id"], cfg=cfg)
            again = "STOPPED_TWICE"
        except work_runs.RunError as exc:
            again = exc.status
        statuses = [call["status"] for call in list_tool_calls(cfg)["tool_calls"] if call.get("run_id") == run["run_id"]]
        events = [event["type"] for event in work_runs.list_run_events(run["run_id"], cfg=cfg)]
        next_state = work_runs.get_run(queued["run_id"], cfg)["state"]
    ok = (
        stopped["state"] == "INTERRUPTED"
        and statuses == ["CANCELLED", "CANCELLED"]
        and events.count("interrupted") == 1
        and again == "ERROR_RUN_ALREADY_FINISHED"
        and next_state == "COMPLETED"
    )
    return ("INTERRUPTED_PENDING_CANCELLED" if ok else "CANCEL_INCOMPLETE"), {
        "call_statuses": statuses,
        "second_stop": again,
        "next_run": next_state,
    }


def _cancel_queued(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    with _inline_runs() as work_runs:
        chat = create_conversation("cancel queued", cfg)["conversation_id"]
        work_runs.submit_message(chat, "/status", cfg=cfg)
        queued = work_runs.submit_message(chat, "What is in the library?", cfg=cfg)["run"]
        before = work_runs.get_run(queued["run_id"], cfg)["state"]
        stopped = work_runs.stop_run(queued["run_id"], cfg=cfg)
        events = [event["type"] for event in work_runs.list_run_events(queued["run_id"], cfg=cfg)]
    ok = before == "QUEUED" and stopped["state"] == "CANCELLED" and "run_started" not in events and events.count("cancelled") == 1
    return (stopped["outcome"] if ok else "QUEUED_RUN_STARTED"), {"before": before, "events": events}


# --- crash_recovery ---------------------------------------------------------------------------------------------


def _step_idempotent_after_crash(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    created = create_checkpoint(
        session_id="s", run_id="r", objective="step", source_sha=SHA, state="EXECUTING", pending_steps=[{"id": "fetch"}], cfg=cfg
    )
    checkpoint_id = created["checkpoint_id"]
    begun = begin_step(checkpoint_id, step_id="fetch", idempotency_key="k-1", cfg=cfg)
    # The process "crashes" with the step in flight: pause, then resume as a recovering process would.
    transition_checkpoint(checkpoint_id, state="PAUSED_PROVIDER", pause_reason="crash", cfg=cfg)
    resumed = resume_checkpoint(checkpoint_id, cfg)
    done = complete_step(checkpoint_id, idempotency_key="k-1", result={"ok": True}, cfg=cfg)
    again = complete_step(checkpoint_id, idempotency_key="k-1", cfg=cfg)
    try:
        begin_step(checkpoint_id, step_id="fetch", idempotency_key="k-1", cfg=cfg)
        replay = "STEP_REPLAYED"
    except CheckpointError as exc:
        replay = exc.status
    ok = (
        resumed["in_flight_step"] == begun["in_flight_step"]
        and done["in_flight_step"] is None
        and len(again["completed_steps"]) == 1
        and again == done
        and replay == "ERROR_CHECKPOINT_STEP_ALREADY_COMPLETED"
    )
    return ("NO_DUPLICATE_STEP" if ok else "STEP_DUPLICATED_OR_LOST"), {
        "completed_steps": len(again["completed_steps"]),
        "replay": replay,
    }


def _accepted_op_not_replayed(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    created = create_checkpoint(session_id="s", run_id="r", objective="answer", source_sha=SHA, state="EXECUTING", cfg=cfg)
    counter = {"n": 0}

    def operation() -> dict[str, Any]:
        counter["n"] += 1
        return {"answer": "accepted"}

    first = execute_with_provider_checkpoint(
        created["checkpoint_id"], provider="mock", model="mock", operation=operation, operation_key="answer:1", cfg=cfg
    )
    replay = execute_with_provider_checkpoint(
        created["checkpoint_id"], provider="mock", model="mock", operation=operation, operation_key="answer:1", cfg=cfg
    )
    ok = (
        first["status"] == "PROVIDER_EXECUTION_OK"
        and replay["result"] == {"answer": "accepted"}
        and replay["attempt_id"] == first["attempt_id"]
        and counter["n"] == 1
    )
    return (replay["status"] if ok else "OPERATION_REPLAYED"), {"operation_calls": counter["n"]}


# --- partial_tool_failure ---------------------------------------------------------------------------------------


def _partial_failure_retry_only_failed(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    from .response_flow import get_pending_interaction, resolve_interaction

    with _inline_runs() as work_runs, _flaky_tool(work_runs) as flaky:
        chat = create_conversation("partial", cfg)["conversation_id"]
        run = work_runs.submit_message(chat, "/status\n/flaky", mode="auto", cfg=cfg)["run"]
        waiting = work_runs.get_run(run["run_id"], cfg)["state"]
        question = get_pending_interaction(chat, cfg)
        resolve_interaction(
            question["interaction_id"], expected_version=question["version"], resolved_by="user", option_id="retry_failed", cfg=cfg
        )
        work_runs.resume_waiting_run(run["run_id"], cfg)
        final = work_runs.get_run(run["run_id"], cfg)["state"]
        grouped = _calls_by_tool(cfg, run["run_id"])
        handler_calls = flaky["n"]
    flaky_calls = grouped.get("benchmark.flaky", [])
    status_calls = grouped.get("source_library.status", [])
    ok = (
        waiting == "AWAITING_INPUT"
        and final == "COMPLETED"
        and [call["status"] for call in flaky_calls] == ["FAILED", "EXECUTED"]
        and flaky_calls[1].get("retry_of") == flaky_calls[0]["tool_call_id"]
        and [call["status"] for call in status_calls] == ["EXECUTED"]
        and handler_calls == 2
    )
    return ("COMPLETED_SUCCESSFUL_NOT_REPEATED" if ok else "RETRY_REPEATED_OR_INCOMPLETE"), {
        "flaky_statuses": [call["status"] for call in flaky_calls],
        "status_calls": len(status_calls),
        "handler_calls": handler_calls,
    }


# --- network_interruption ---------------------------------------------------------------------------------------


def _local_model_call() -> dict[str, Any]:
    from .model_router import generate_answer

    chunk = [{"chunk_id": "chunk_net", "source_id": "src_net", "text": "Recovery keeps identity."}]
    return generate_answer("What does recovery keep?", chunk, model_provider="openai_compatible_local")


def _network_drop_pauses_checkpoint(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    created = create_checkpoint(session_id="s", run_id="r", objective="answer", source_sha=SHA, state="EXECUTING", cfg=cfg)
    with _environment(MUCARO_AI_LOCAL_MODEL_URL=CLOSED_LOOPBACK_URL):
        result = execute_with_provider_checkpoint(
            created["checkpoint_id"],
            provider="openai_compatible_local",
            model="local",
            operation=_local_model_call,
            operation_key="answer:net",
            cfg=cfg,
        )
    stored = get_checkpoint(created["checkpoint_id"], cfg) or {}
    attempt = (stored.get("metadata") or {}).get("provider_attempts", {}).get(result.get("attempt_id"), {})
    identity = ("checkpoint_id", "session_id", "run_id", "source_sha", "objective")
    ok = (
        result["status"] == "PAUSED_PROVIDER"
        and result["provider_error"] == "ERROR_LOCAL_MODEL_UNAVAILABLE"
        and stored.get("state") == "PAUSED_PROVIDER"
        and all(stored.get(field) == created[field] for field in identity)
        and attempt.get("state") == "INTERRUPTED_UNKNOWN"
        and attempt.get("attempts") == 1
    )
    return ("PAUSED_PROVIDER_IDENTITY_PRESERVED" if ok else "NETWORK_DROP_NOT_CONTAINED"), {
        "provider_error": result.get("provider_error"),
        "attempt_state": attempt.get("state"),
    }


def _interrupted_op_retried_on_resume(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    created = create_checkpoint(session_id="s", run_id="r", objective="answer", source_sha=SHA, state="EXECUTING", cfg=cfg)
    checkpoint_id = created["checkpoint_id"]

    def outage() -> dict[str, Any]:
        raise ModelProviderError("ERROR_MODEL_PROVIDER_UNAVAILABLE", "connection reset")

    paused = execute_with_provider_checkpoint(
        checkpoint_id, provider="openai_compatible_local", model="local", operation=outage, operation_key="answer:retry", cfg=cfg
    )
    resume_with_provider(checkpoint_id, provider="mock", model="mock", cfg=cfg)
    counter = {"n": 0}

    def recovered() -> dict[str, Any]:
        counter["n"] += 1
        return {"answer": "after resume"}

    retried = execute_with_provider_checkpoint(
        checkpoint_id, provider="mock", model="mock", operation=recovered, operation_key="answer:retry", cfg=cfg
    )
    replay = execute_with_provider_checkpoint(
        checkpoint_id, provider="mock", model="mock", operation=recovered, operation_key="answer:retry", cfg=cfg
    )
    attempt = (get_checkpoint(checkpoint_id, cfg) or {})["metadata"]["provider_attempts"][retried["attempt_id"]]
    ok = (
        paused["status"] == "PAUSED_PROVIDER"
        and retried["status"] == "PROVIDER_EXECUTION_OK"
        and retried["attempt_id"] == paused["attempt_id"]
        and replay["status"] == "PROVIDER_EXECUTION_ALREADY_ACCEPTED"
        and attempt["state"] == "ACCEPTED"
        and attempt["attempts"] == 2
        and counter["n"] == 1
    )
    return ("ACCEPTED_ONCE_AFTER_RESUME" if ok else "RETRY_LOST_OR_DUPLICATED"), {
        "attempts": attempt.get("attempts"),
        "operation_calls": counter["n"],
    }


# --- offline_transition -----------------------------------------------------------------------------------------


def _offline_route_blocks_remote(_cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    from .model_routing_policy import plan_model_route

    remote = {name for name, spec in MODEL_PROVIDER_SPECS.items() if spec.locality != "local"}
    with _environment(MUCARO_AI_LIVE_MODE="1", ANTHROPIC_API_KEY="benchmark-placeholder"):
        online = plan_model_route(task_type="cited_qa", allow_live=True)
        offline = plan_model_route(task_type="cited_qa", allow_live=True, system_offline=True)
    offline_rows = {row["provider"]: row for row in offline["candidates"]}
    online_rows = {row["provider"]: row for row in online["candidates"]}
    ok = (
        bool(remote)
        and all("SYSTEM_OFFLINE" in offline_rows[name]["reasons"] and not offline_rows[name]["eligible"] for name in remote)
        and not any("SYSTEM_OFFLINE" in row["reasons"] for row in online_rows.values())
        and offline["invariants"].get("no_remote_provider_while_offline") is True
        and offline["status"] == "MODEL_ROUTE_READY"
        and MODEL_PROVIDER_SPECS[offline["selected_provider"]].locality == "local"
    )
    return ("REMOTE_INELIGIBLE_ONLY_WHILE_OFFLINE" if ok else "REMOTE_ROUTE_WHILE_OFFLINE"), {
        "offline_selected": offline.get("selected_provider"),
        "remote_providers": sorted(remote),
    }


def _offline_manifest_flips(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    from .offline import build_offline_manifest

    online = build_offline_manifest(cfg=cfg)
    offline = build_offline_manifest(system_offline=True, cfg=cfg)
    on = {row["capability"]: row["state_now"] for row in online["capabilities"]}
    off = {row["capability"]: row["state_now"] for row in offline["capabilities"]}
    ok = (
        online["arithmetic_closes"] is True
        and offline["arithmetic_closes"] is True
        and set(on) == set(off)
        and on["tool:web.fetch"] != "UNAVAILABLE_OFFLINE"
        and off["tool:web.fetch"] == "UNAVAILABLE_OFFLINE"
        and on["tool:source_library.status"] == off["tool:source_library.status"] == "AVAILABLE_LOCALLY"
        and off["model:mock"] == "AVAILABLE_LOCALLY"
    )
    return ("WEB_FETCH_UNAVAILABLE_OFFLINE" if ok else "OFFLINE_MANIFEST_WRONG"), {
        "web_fetch_online": on.get("tool:web.fetch"),
        "web_fetch_offline": off.get("tool:web.fetch"),
    }


SUBPROCESS_CASES = {"APP_RESTART_RECONCILES", "WORKFLOW_CHECKPOINT_SURVIVES_PROCESS", "RESUME_WAITING_AFTER_RESTART"}

CASES: dict[str, Callable[..., tuple[str, dict[str, Any]]]] = {
    "APP_RESTART_RECONCILES": _app_restart_reconciles,
    "WORKFLOW_CHECKPOINT_SURVIVES_PROCESS": _workflow_checkpoint_survives_process,
    "CORRUPT_CHECKPOINT_FAILS_CLOSED": _corrupt_checkpoint_fails_closed,
    "RESUME_WAITING_AFTER_RESTART": _resume_waiting_after_restart,
    "CANCEL_AWAITING_RUN": _cancel_awaiting_run,
    "CANCEL_QUEUED": _cancel_queued,
    "STEP_IDEMPOTENT_AFTER_CRASH": _step_idempotent_after_crash,
    "ACCEPTED_OP_NOT_REPLAYED": _accepted_op_not_replayed,
    "PARTIAL_FAILURE_RETRY_ONLY_FAILED": _partial_failure_retry_only_failed,
    "NETWORK_DROP_PAUSES_CHECKPOINT": _network_drop_pauses_checkpoint,
    "INTERRUPTED_OP_RETRIED_ON_RESUME": _interrupted_op_retried_on_resume,
    "OFFLINE_ROUTE_BLOCKS_REMOTE": _offline_route_blocks_remote,
    "OFFLINE_MANIFEST_FLIPS": _offline_manifest_flips,
}


def _run_case(case: dict[str, Any], workspace: Path, project_root: Path) -> dict[str, Any]:
    case_id = case["case_id"]
    handler = CASES.get(case_id)
    root = workspace / case_id.lower()
    root.mkdir(parents=True, exist_ok=True)
    if handler is None:
        observed, evidence = "NO_HANDLER", {}
    else:
        try:
            if handler is _SUBPROCESS_HANDLERS.get(case_id):
                observed, evidence = handler(_settings(root), root, project_root=project_root)
            else:
                observed, evidence = handler(_settings(root), root)
        except Exception as exc:  # a crashing case is a failed case, recorded with its error type
            observed, evidence = f"CASE_ERROR:{type(exc).__name__}", {"detail": str(exc)[:400]}
    return {
        "case_id": case_id,
        "category": case["category"],
        "expected_outcome": case["expected_outcome"],
        "observed_outcome": observed,
        "passed": observed == case["expected_outcome"],
        "evidence": evidence,
    }


_SUBPROCESS_HANDLERS = {case_id: CASES[case_id] for case_id in SUBPROCESS_CASES}


def certify_recovery_benchmark(project_root: Path, workspace: Path, *, head_sha: str | None = None) -> dict[str, Any]:
    project_root = project_root.resolve()
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    manifest_bytes = (project_root / MANIFEST_PATH).read_bytes()
    manifest = json.loads(manifest_bytes)
    cases = manifest.get("cases") or []
    case_ids = [case.get("case_id") for case in cases]
    categories = {case.get("category") for case in cases}
    required = int(manifest.get("required_case_count") or 0)
    denominator_checks = {
        "manifest_schema": manifest.get("schema_version") == MANIFEST_SCHEMA_VERSION,
        "case_count_matches_declaration": len(cases) == required and required > 0,
        "case_ids_unique": len(set(case_ids)) == len(case_ids),
        "every_case_has_a_handler": all(case_id in CASES for case_id in case_ids),
        "every_handler_is_declared": set(CASES) == set(case_ids),
        "required_categories_declared": list(manifest.get("required_categories") or []) == list(REQUIRED_CATEGORIES),
        "every_required_category_covered": set(REQUIRED_CATEGORIES) <= categories,
    }
    rows: list[dict[str, Any]] = []
    if all(denominator_checks.values()):
        rows = [_run_case(case, workspace, project_root) for case in cases]
    passed = sum(1 for row in rows if row["passed"])
    arithmetic = {
        "case_count_expected": required,
        "case_count_observed": len(rows),
        "case_pass": passed,
        "case_fail": len(rows) - passed,
        "arithmetic_closes": passed + (len(rows) - passed) == len(rows) == required,
    }
    terminal_checks = {
        "denominator_checks_pass": all(denominator_checks.values()),
        "all_cases_pass": bool(rows) and passed == required,
        "case_identity_matches_manifest": [row["case_id"] for row in rows] == case_ids,
        "every_required_category_covered": set(REQUIRED_CATEGORIES) <= {row["category"] for row in rows if row["passed"]},
        "zero_unexpected_outcomes": bool(rows) and all(row["passed"] for row in rows),
        "arithmetic_closes": arithmetic["arithmetic_closes"],
    }
    receipt = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "status": STATUS_OK if all(terminal_checks.values()) else STATUS_FAILED,
        "benchmark_id": manifest.get("benchmark_id"),
        "head_sha": head_sha,
        "executed_utc": utc_now(),
        "benchmark_manifest_path": str(MANIFEST_PATH),
        "benchmark_manifest_sha256": _sha256_bytes(manifest_bytes),
        "denominator_checks": denominator_checks,
        "arithmetic": arithmetic,
        "terminal_checks": terminal_checks,
        "case_rows": rows,
        "failed_checks": [name for name, ok in {**denominator_checks, **terminal_checks}.items() if not ok],
        "limitations": list(manifest.get("limitations") or []),
    }
    receipt["receipt_sha256"] = _stable_hash(receipt)
    return receipt
