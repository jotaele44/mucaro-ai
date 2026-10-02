from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import jsonschema

from .config import Settings, settings
from .db import get_connection, init_db, upsert_payload
from .utils import stable_id, utc_now

CHECKPOINT_SCHEMA_VERSION = "mucaro-agent-checkpoint/1.0"
CHECKPOINT_STATES = {
    "PLANNING",
    "READY_FOR_APPROVAL",
    "EXECUTING",
    "PAUSED_PROVIDER",
    "PAUSED_RESOURCE",
    "BLOCKED_PERMISSION",
    "BLOCKED_EXTERNAL",
    "FAILED",
    "COMPLETED",
}
TERMINAL_STATES = {"FAILED", "COMPLETED"}
RESUMABLE_STATES = {"PAUSED_PROVIDER", "PAUSED_RESOURCE", "BLOCKED_EXTERNAL"}
# A checkpoint is born before any work runs: never terminal, and never paused or blocked without a reason.
INITIAL_STATES = frozenset({"PLANNING", "READY_FOR_APPROVAL", "EXECUTING"})
# Legal state changes. A checkpoint cannot jump anywhere: COMPLETED must be reached through EXECUTING,
# and terminal states have no outgoing edges. Same-state EXECUTING -> EXECUTING records step progress.
TRANSITIONS: dict[str, frozenset[str]] = {
    "PLANNING": frozenset({"READY_FOR_APPROVAL", "EXECUTING", "BLOCKED_PERMISSION", "BLOCKED_EXTERNAL", "PAUSED_PROVIDER", "PAUSED_RESOURCE", "FAILED"}),
    "READY_FOR_APPROVAL": frozenset({"PLANNING", "EXECUTING", "BLOCKED_PERMISSION", "FAILED"}),
    "EXECUTING": frozenset({"EXECUTING", "PLANNING", "PAUSED_PROVIDER", "PAUSED_RESOURCE", "BLOCKED_PERMISSION", "BLOCKED_EXTERNAL", "COMPLETED", "FAILED"}),
    "PAUSED_PROVIDER": frozenset({"EXECUTING", "PAUSED_RESOURCE", "BLOCKED_EXTERNAL", "FAILED"}),
    "PAUSED_RESOURCE": frozenset({"EXECUTING", "PAUSED_PROVIDER", "BLOCKED_EXTERNAL", "FAILED"}),
    "BLOCKED_PERMISSION": frozenset({"READY_FOR_APPROVAL", "EXECUTING", "FAILED"}),
    "BLOCKED_EXTERNAL": frozenset({"EXECUTING", "PAUSED_PROVIDER", "PAUSED_RESOURCE", "FAILED"}),
    "FAILED": frozenset(),
    "COMPLETED": frozenset(),
}
CONTRADICTION_OPEN = "UNRESOLVED"
CONTRADICTION_CLOSED = "ADJUDICATED"
_UNSET: Any = object()


class CheckpointError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def create_checkpoint(
    *,
    session_id: str,
    run_id: str,
    objective: str,
    source_sha: str,
    state: str = "PLANNING",
    completed_steps: list[dict[str, Any]] | None = None,
    pending_steps: list[dict[str, Any]] | None = None,
    contradictions: list[dict[str, Any]] | None = None,
    provider: str | None = None,
    model: str | None = None,
    metadata: dict[str, Any] | None = None,
    cfg: Settings = settings,
) -> dict[str, Any]:
    if state not in CHECKPOINT_STATES:
        raise CheckpointError("ERROR_CHECKPOINT_STATE", f"unsupported checkpoint state: {state}")
    if state not in INITIAL_STATES:
        raise CheckpointError("ERROR_CHECKPOINT_INITIAL_STATE", f"a checkpoint cannot start in {state}")
    if not session_id or not run_id or not objective.strip():
        raise CheckpointError("ERROR_CHECKPOINT_IDENTITY", "session_id, run_id, and objective are required")
    if len(source_sha) != 40 or any(char not in "0123456789abcdef" for char in source_sha):
        raise CheckpointError("ERROR_CHECKPOINT_SOURCE_SHA", "source_sha must be a lowercase 40-character Git SHA")
    cfg.ensure_dirs()
    init_db(cfg.sqlite_path)
    now = utc_now()
    checkpoint = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "checkpoint_id": stable_id("checkpoint", session_id, run_id, source_sha, now, length=16),
        "session_id": session_id,
        "run_id": run_id,
        "objective": objective.strip(),
        "state": state,
        "created_at": now,
        "updated_at": now,
        "source_sha": source_sha,
        "sequence": 0,
        "completed_steps": list(completed_steps or []),
        "pending_steps": list(pending_steps or []),
        "contradictions": _merged_contradictions([], contradictions or []),
        "pause_reason": None,
        "provider": provider,
        "model": model,
        "metadata": dict(metadata or {}),
    }
    _validate_payload(checkpoint, cfg)
    upsert_payload("agent_checkpoints", "checkpoint_id", checkpoint, cfg.sqlite_path)
    return checkpoint


@lru_cache(maxsize=1)
def _schema() -> dict[str, Any]:
    # Application code, not project data: resolve relative to this module, never cfg.project_root.
    path = Path(__file__).resolve().parents[2] / "schemas" / "agent_checkpoint.schema.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CheckpointError("ERROR_CHECKPOINT_SCHEMA_UNAVAILABLE", f"{path}: {exc}") from exc


def _validate_payload(checkpoint: dict[str, Any], cfg: Settings) -> None:
    errors = sorted(jsonschema.Draft202012Validator(_schema()).iter_errors(checkpoint), key=lambda e: list(e.absolute_path))
    if errors:
        where = "/".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise CheckpointError("ERROR_CHECKPOINT_SCHEMA", f"{where}: {errors[0].message}")


def _decode(raw: str, cfg: Settings) -> dict[str, Any]:
    try:
        checkpoint = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CheckpointError("ERROR_CHECKPOINT_CORRUPT", f"checkpoint payload is not valid JSON: {exc}") from exc
    if not isinstance(checkpoint, dict):
        raise CheckpointError("ERROR_CHECKPOINT_CORRUPT", "checkpoint payload is not an object")
    _validate_payload(checkpoint, cfg)
    return checkpoint


def get_checkpoint(checkpoint_id: str, cfg: Settings = settings) -> dict[str, Any] | None:
    init_db(cfg.sqlite_path)
    with get_connection(cfg.sqlite_path) as conn:
        row = conn.execute("SELECT payload_json FROM agent_checkpoints WHERE checkpoint_id = ?", (checkpoint_id,)).fetchone()
    return None if row is None else _decode(row["payload_json"], cfg)


def list_checkpoints(cfg: Settings = settings) -> list[dict[str, Any]]:
    init_db(cfg.sqlite_path)
    with get_connection(cfg.sqlite_path) as conn:
        rows = conn.execute("SELECT payload_json FROM agent_checkpoints ORDER BY rowid ASC").fetchall()
    # Same fail-closed decode as get_checkpoint: a corrupt or schema-invalid row is a CheckpointError.
    return [_decode(row["payload_json"], cfg) for row in rows]


def _mutate(checkpoint_id: str, change: Callable[[dict[str, Any]], dict[str, Any] | None], cfg: Settings, *, expected_sequence: int | None = None) -> dict[str, Any]:
    """Atomic read-modify-write. BEGIN IMMEDIATE serializes writers across threads and processes, so
    two concurrent transitions cannot both act on the same prior state. A change that returns None
    writes nothing: the stored checkpoint, its sequence included, stays as it was."""
    init_db(cfg.sqlite_path)
    conn = get_connection(cfg.sqlite_path)
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT payload_json FROM agent_checkpoints WHERE checkpoint_id = ?", (checkpoint_id,)).fetchone()
        if row is None:
            raise CheckpointError("ERROR_CHECKPOINT_NOT_FOUND", checkpoint_id)
        current = _decode(row["payload_json"], cfg)
        if expected_sequence is not None and current["sequence"] != expected_sequence:
            raise CheckpointError("ERROR_CHECKPOINT_CONFLICT", f"expected sequence {expected_sequence}, found {current['sequence']}")
        updated = change(dict(current))
        if updated is None:
            conn.execute("ROLLBACK")
            return current
        updated["sequence"] = int(current["sequence"]) + 1
        updated["updated_at"] = utc_now()
        _validate_payload(updated, cfg)
        conn.execute(
            "UPDATE agent_checkpoints SET payload_json = ? WHERE checkpoint_id = ?",
            (json.dumps(updated, ensure_ascii=False, sort_keys=True), checkpoint_id),
        )
        conn.execute("COMMIT")
        return updated
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def _contradiction_ids(items: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("id")) for item in items if isinstance(item, dict) and item.get("id") is not None}


def _merged_contradictions(current: list[dict[str, Any]], supplied: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stored contradictions survive exactly as stored; new ones enter UNRESOLVED.

    Only adjudicate_contradiction closes one, so a caller cannot rewrite a record to ADJUDICATED and
    drop its claims.
    """
    stored = {str(item["id"]): item for item in current if isinstance(item, dict) and item.get("id") is not None}
    seen: set[str] = set()
    for item in supplied:
        item_id = item.get("id") if isinstance(item, dict) else None
        if not isinstance(item_id, str) or not item_id:
            raise CheckpointError("ERROR_CHECKPOINT_CONTRADICTION_ID", "every contradiction needs a non-empty string id")
        if item_id in seen:
            raise CheckpointError("ERROR_CHECKPOINT_CONTRADICTION_DUPLICATE", item_id)
        seen.add(item_id)
        if item_id in stored:
            if item != stored[item_id]:
                raise CheckpointError("ERROR_CHECKPOINT_CONTRADICTION_REWRITTEN", f"{item_id} changes only through adjudicate_contradiction")
        elif item.get("state") != CONTRADICTION_OPEN:
            raise CheckpointError("ERROR_CHECKPOINT_CONTRADICTION_STATE", f"new contradiction {item_id} must be {CONTRADICTION_OPEN}")
    dropped = sorted(set(stored) - seen)
    if dropped:
        raise CheckpointError("ERROR_CHECKPOINT_CONTRADICTION_DROPPED", f"contradictions must survive; missing: {dropped}")
    return [dict(item) for item in supplied]


def _adjudicated(item: dict[str, Any]) -> bool:
    # Closed only the way adjudicate_contradiction closes it; any other state or a missing resolution is open.
    return item.get("state") == CONTRADICTION_CLOSED and all(str(item.get(key) or "").strip() for key in ("resolution", "rationale", "adjudicated_by"))


def transition_checkpoint(
    checkpoint_id: str,
    *,
    state: str,
    pause_reason: str | None = None,
    completed_steps: list[dict[str, Any]] | None = None,
    pending_steps: list[dict[str, Any]] | None = None,
    contradictions: list[dict[str, Any]] | None = None,
    provider: str | None = _UNSET,
    model: str | None = _UNSET,
    expected_sequence: int | None = None,
    cfg: Settings = settings,
) -> dict[str, Any]:
    if state not in CHECKPOINT_STATES:
        raise CheckpointError("ERROR_CHECKPOINT_STATE", f"unsupported checkpoint state: {state}")
    if (state.startswith("PAUSED_") or state.startswith("BLOCKED_")) and not (pause_reason or "").strip():
        raise CheckpointError("ERROR_CHECKPOINT_PAUSE_REASON", "paused/blocked states require pause_reason")

    def change(current: dict[str, Any]) -> dict[str, Any]:
        if current["state"] in TERMINAL_STATES:
            raise CheckpointError("ERROR_CHECKPOINT_TERMINAL", "terminal checkpoint cannot transition")
        if state not in TRANSITIONS[current["state"]]:
            raise CheckpointError("ERROR_CHECKPOINT_TRANSITION", f"illegal transition {current['state']} -> {state}")
        if contradictions is not None:
            current["contradictions"] = _merged_contradictions(current["contradictions"], contradictions)
        if state == "COMPLETED":
            if current.get("in_flight_step"):
                raise CheckpointError("ERROR_CHECKPOINT_STEP_IN_FLIGHT", "cannot complete while a step is in flight")
            open_ids = sorted(str(c.get("id")) for c in current["contradictions"] if not _adjudicated(c))
            if open_ids:
                raise CheckpointError("ERROR_CHECKPOINT_OPEN_CONTRADICTIONS", f"unadjudicated contradictions: {open_ids}")
        current["state"] = state
        current["pause_reason"] = pause_reason.strip() if pause_reason else None
        if completed_steps is not None:
            current["completed_steps"] = list(completed_steps)
        if pending_steps is not None:
            current["pending_steps"] = list(pending_steps)
        if provider is not _UNSET:
            current["provider"] = provider
        if model is not _UNSET:
            current["model"] = model
        return current

    return _mutate(checkpoint_id, change, cfg, expected_sequence=expected_sequence)



def install_pending_task_dag_state(
    checkpoint_id: str,
    *,
    tasks: list[dict[str, Any]],
    dag_metadata: dict[str, Any],
    expected_sequence: int,
    cfg: Settings = settings,
) -> dict[str, Any]:
    """Atomically install the initial task DAG without creating a second persistence authority.

    Task definitions live in pending_steps; metadata contains only DAG identity/topology summary.
    Existing progress is never overwritten. Replanning/supersession is a separate operation.
    """
    if not isinstance(tasks, list) or not tasks:
        raise CheckpointError("ERROR_CHECKPOINT_TASK_DAG_EMPTY", "task DAG requires at least one task")
    if not isinstance(dag_metadata, dict) or not dag_metadata:
        raise CheckpointError("ERROR_CHECKPOINT_TASK_DAG_METADATA", "task DAG metadata is required")

    def change(current: dict[str, Any]) -> dict[str, Any]:
        if current["state"] in TERMINAL_STATES:
            raise CheckpointError("ERROR_CHECKPOINT_TERMINAL", "terminal checkpoint cannot install a task DAG")
        if current["state"] != "PLANNING":
            raise CheckpointError("ERROR_CHECKPOINT_TASK_DAG_STATE", f"task DAG installs only in PLANNING, not {current['state']}")
        if current.get("in_flight_step"):
            raise CheckpointError("ERROR_CHECKPOINT_STEP_IN_FLIGHT", "cannot install a task DAG while a step is in flight")
        if current.get("pending_steps") or current.get("completed_steps"):
            raise CheckpointError("ERROR_CHECKPOINT_TASK_DAG_PROGRESS_EXISTS", "existing pending/completed progress cannot be overwritten")
        metadata = dict(current.get("metadata") or {})
        if metadata.get("task_dag"):
            raise CheckpointError("ERROR_CHECKPOINT_TASK_DAG_EXISTS", "task DAG is already installed")
        current["pending_steps"] = [dict(task) for task in tasks]
        metadata["task_dag"] = dict(dag_metadata)
        current["metadata"] = metadata
        return current

    return _mutate(checkpoint_id, change, cfg, expected_sequence=expected_sequence)

def resume_checkpoint(checkpoint_id: str, cfg: Settings = settings, *, provider: str | None = _UNSET, model: str | None = _UNSET) -> dict[str, Any]:
    checkpoint = get_checkpoint(checkpoint_id, cfg)
    if checkpoint is None:
        raise CheckpointError("ERROR_CHECKPOINT_NOT_FOUND", checkpoint_id)
    if checkpoint.get("state") not in RESUMABLE_STATES:
        raise CheckpointError("ERROR_CHECKPOINT_NOT_RESUMABLE", f"state is not resumable: {checkpoint.get('state')}")
    # expected_sequence makes "resume exactly once" real: a concurrent resumer loses with ERROR_CHECKPOINT_CONFLICT.
    return transition_checkpoint(checkpoint_id, state="EXECUTING", provider=provider, model=model, expected_sequence=checkpoint["sequence"], cfg=cfg)


def begin_step(checkpoint_id: str, *, step_id: str, idempotency_key: str, cfg: Settings = settings) -> dict[str, Any]:
    """Record the step about to run so a crash mid-step is detectable on resume."""
    if not step_id or not idempotency_key:
        raise CheckpointError("ERROR_CHECKPOINT_STEP", "step_id and idempotency_key are required")

    def change(current: dict[str, Any]) -> dict[str, Any]:
        if current["state"] != "EXECUTING":
            raise CheckpointError("ERROR_CHECKPOINT_STEP_STATE", f"steps run only in EXECUTING, not {current['state']}")
        if current.get("in_flight_step"):
            raise CheckpointError("ERROR_CHECKPOINT_STEP_IN_FLIGHT", f"step already in flight: {current['in_flight_step']['step_id']}")
        if any(done.get("idempotency_key") == idempotency_key for done in current["completed_steps"]):
            raise CheckpointError("ERROR_CHECKPOINT_STEP_ALREADY_COMPLETED", idempotency_key)
        current["in_flight_step"] = {"step_id": step_id, "idempotency_key": idempotency_key, "started_at": utc_now()}
        return current

    return _mutate(checkpoint_id, change, cfg)


def complete_step(checkpoint_id: str, *, idempotency_key: str, result: dict[str, Any] | None = None, cfg: Settings = settings) -> dict[str, Any]:
    """Finish the in-flight step. Completing the same idempotency key twice is a no-op, not a duplicate."""
    def change(current: dict[str, Any]) -> dict[str, Any] | None:
        if any(done.get("idempotency_key") == idempotency_key for done in current["completed_steps"]):
            return None
        flight = current.get("in_flight_step")
        if not flight or flight["idempotency_key"] != idempotency_key:
            raise CheckpointError("ERROR_CHECKPOINT_STEP_NOT_IN_FLIGHT", idempotency_key)
        current["completed_steps"] = [*current["completed_steps"], {"id": flight["step_id"], "idempotency_key": idempotency_key, "status": "PASS", "result": dict(result or {}), "completed_at": utc_now()}]
        current["pending_steps"] = [step for step in current["pending_steps"] if step.get("id") != flight["step_id"]]
        current["in_flight_step"] = None
        return current

    return _mutate(checkpoint_id, change, cfg)


def record_contradiction(checkpoint_id: str, *, contradiction_id: str, claims: list[str], cfg: Settings = settings) -> dict[str, Any]:
    def change(current: dict[str, Any]) -> dict[str, Any]:
        if current["state"] in TERMINAL_STATES:
            raise CheckpointError("ERROR_CHECKPOINT_TERMINAL", "terminal checkpoint cannot record contradictions")
        if contradiction_id in _contradiction_ids(current["contradictions"]):
            raise CheckpointError("ERROR_CHECKPOINT_CONTRADICTION_EXISTS", contradiction_id)
        current["contradictions"] = [*current["contradictions"], {"id": contradiction_id, "state": CONTRADICTION_OPEN, "claims": list(claims), "recorded_at": utc_now()}]
        return current

    return _mutate(checkpoint_id, change, cfg)



PROVIDER_ATTEMPT_STARTED = "STARTED"
PROVIDER_ATTEMPT_ACCEPTED = "ACCEPTED"


def record_provider_attempt(
    checkpoint_id: str,
    *,
    attempt_id: str,
    record: dict[str, Any],
    claim: bool = False,
    reclaim_started: bool = False,
    cfg: Settings = settings,
) -> dict[str, Any]:
    """Atomically write one provider operation attempt under ``metadata.provider_attempts``.

    An ACCEPTED attempt is final and is never overwritten, so an accepted operation is never run again.
    ``claim=True`` marks the attempt as started; it refuses a terminal checkpoint and, unless
    ``reclaim_started``, an attempt another executor already started, so two executors cannot both run it."""

    def change(current: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(current.get("metadata") or {})
        attempts = dict(metadata.get("provider_attempts") or {})
        existing = attempts.get(attempt_id) or {}
        if existing.get("state") == PROVIDER_ATTEMPT_ACCEPTED:
            raise CheckpointError("ERROR_CHECKPOINT_ATTEMPT_ACCEPTED", attempt_id)
        entry = dict(record)
        if claim:
            if current["state"] in TERMINAL_STATES:
                raise CheckpointError("ERROR_CHECKPOINT_TERMINAL", "terminal checkpoint cannot start a provider attempt")
            if existing.get("state") == PROVIDER_ATTEMPT_STARTED and not reclaim_started:
                raise CheckpointError("ERROR_CHECKPOINT_ATTEMPT_IN_FLIGHT", f"{attempt_id} started at {existing.get('started_at')} with no recorded outcome")
            entry["state"] = PROVIDER_ATTEMPT_STARTED
            entry["attempts"] = int(existing.get("attempts", 0)) + 1
        attempts[attempt_id] = entry
        metadata["provider_attempts"] = attempts
        current["metadata"] = metadata
        return current

    return _mutate(checkpoint_id, change, cfg)

def adjudicate_contradiction(checkpoint_id: str, *, contradiction_id: str, resolution: str, rationale: str, adjudicated_by: str, cfg: Settings = settings) -> dict[str, Any]:
    """Close a contradiction without erasing it (invariant 7): claims stay, resolution is appended."""
    if not resolution.strip() or not rationale.strip() or not adjudicated_by.strip():
        raise CheckpointError("ERROR_CHECKPOINT_ADJUDICATION", "resolution, rationale, and adjudicated_by are required")

    def change(current: dict[str, Any]) -> dict[str, Any]:
        items = list(current["contradictions"])
        for index, item in enumerate(items):
            if item.get("id") == contradiction_id:
                if item.get("state") == CONTRADICTION_CLOSED:
                    raise CheckpointError("ERROR_CHECKPOINT_CONTRADICTION_CLOSED", contradiction_id)
                items[index] = {**item, "state": CONTRADICTION_CLOSED, "resolution": resolution.strip(), "rationale": rationale.strip(), "adjudicated_by": adjudicated_by.strip(), "adjudicated_at": utc_now()}
                current["contradictions"] = items
                return current
        raise CheckpointError("ERROR_CHECKPOINT_CONTRADICTION_NOT_FOUND", contradiction_id)

    return _mutate(checkpoint_id, change, cfg)
