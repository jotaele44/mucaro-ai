from __future__ import annotations

import json
from typing import Any

from .config import Settings, settings
from .db import get_connection, init_db
from .utils import stable_id, utc_now

GOAL_SCHEMA_VERSION = "mucaro-goal/1.0"
GOAL_STATES = ("ACTIVE", "PAUSED", "COMPLETE", "BLOCKED", "CANCELLED")
TERMINAL_GOAL_STATES = frozenset({"COMPLETE", "CANCELLED"})
GOAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "ACTIVE": frozenset({"ACTIVE", "PAUSED", "COMPLETE", "BLOCKED", "CANCELLED"}),
    "PAUSED": frozenset({"ACTIVE", "CANCELLED"}),
    "BLOCKED": frozenset({"ACTIVE", "PAUSED", "CANCELLED"}),
    "COMPLETE": frozenset(),
    "CANCELLED": frozenset(),
}
MAX_OBJECTIVE_LENGTH = 4000
MAX_VECTOR_LENGTH = 240
MAX_LIST_ITEMS = 32
MAX_LIST_ITEM_LENGTH = 1000


class GoalError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _text(value: Any, label: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GoalError(f"ERROR_GOAL_{label.upper()}_REQUIRED", f"{label} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > max_length:
        raise GoalError(f"ERROR_GOAL_{label.upper()}_TOO_LONG", f"{label} must be at most {max_length} characters")
    return normalized


def _optional_text(value: Any, label: str, *, max_length: int) -> str | None:
    if value is None:
        return None
    return _text(value, label, max_length=max_length)


def _string_list(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or len(value) > MAX_LIST_ITEMS:
        raise GoalError("ERROR_GOAL_LIST_INVALID", f"{label} must contain at most {MAX_LIST_ITEMS} strings")
    result: list[str] = []
    for item in value:
        result.append(_text(item, label, max_length=MAX_LIST_ITEM_LENGTH))
    return result


def _validate_goal(goal: dict[str, Any]) -> None:
    if goal.get("schema_version") != GOAL_SCHEMA_VERSION:
        raise GoalError("ERROR_GOAL_SCHEMA", "unsupported goal schema version")
    _text(goal.get("goal_id"), "goal_id", max_length=160)
    _text(goal.get("objective"), "objective", max_length=MAX_OBJECTIVE_LENGTH)
    _text(goal.get("active_vector"), "active_vector", max_length=MAX_VECTOR_LENGTH)
    if goal.get("status") not in GOAL_STATES:
        raise GoalError("ERROR_GOAL_STATUS_INVALID", str(goal.get("status")))
    for label in ("assumptions", "gaps", "variables"):
        _string_list(goal.get(label), label)
    if not isinstance(goal.get("metadata"), dict):
        raise GoalError("ERROR_GOAL_METADATA_INVALID", "metadata must be an object")
    if not isinstance(goal.get("is_current"), bool):
        raise GoalError("ERROR_GOAL_CURRENT_INVALID", "is_current must be boolean")
    for label in ("created_at", "updated_at"):
        _text(goal.get(label), label, max_length=80)


def _decode(row: Any) -> dict[str, Any]:
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise GoalError("ERROR_GOAL_CORRUPT", "stored goal payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise GoalError("ERROR_GOAL_CORRUPT", "stored goal payload is not an object")
    _validate_goal(payload)
    return payload


def _rows(conn: Any) -> list[dict[str, Any]]:
    return [_decode(row) for row in conn.execute("SELECT payload_json FROM goals ORDER BY rowid ASC").fetchall()]


def _write_goal(conn: Any, goal: dict[str, Any]) -> None:
    _validate_goal(goal)
    conn.execute(
        "INSERT OR REPLACE INTO goals (goal_id, payload_json) VALUES (?, ?)",
        (goal["goal_id"], json.dumps(goal, ensure_ascii=False, sort_keys=True)),
    )


def _append_event(conn: Any, goal_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    row = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM goal_events WHERE goal_id = ?", (goal_id,)).fetchone()
    seq = int(row["next_seq"])
    event = {
        "schema_version": "mucaro-goal-event/1.0",
        "goal_id": goal_id,
        "seq": seq,
        "event_type": event_type,
        "created_at": utc_now(),
        "payload": payload,
    }
    conn.execute(
        "INSERT INTO goal_events (goal_id, seq, event_type, payload_json) VALUES (?, ?, ?, ?)",
        (goal_id, seq, event_type, json.dumps(event, ensure_ascii=False, sort_keys=True)),
    )
    return event


def list_goals(cfg: Settings = settings, *, status: str | None = None) -> dict[str, Any]:
    if status is not None and status not in GOAL_STATES:
        raise GoalError("ERROR_GOAL_STATUS_INVALID", status)
    init_db(cfg.sqlite_path)
    with get_connection(cfg.sqlite_path) as conn:
        goals = _rows(conn)
    if status is not None:
        goals = [goal for goal in goals if goal["status"] == status]
    current = next((goal for goal in goals if goal["is_current"]), None)
    return {
        "status": "GOALS_OK",
        "goals": goals,
        "current_goal_id": current["goal_id"] if current else None,
        "goal_count": len(goals),
    }


def get_goal(goal_id: str, cfg: Settings = settings) -> dict[str, Any] | None:
    goal_id = _text(goal_id, "goal_id", max_length=160)
    init_db(cfg.sqlite_path)
    with get_connection(cfg.sqlite_path) as conn:
        row = conn.execute("SELECT payload_json FROM goals WHERE goal_id = ?", (goal_id,)).fetchone()
    return _decode(row) if row else None


def list_goal_events(goal_id: str, cfg: Settings = settings) -> dict[str, Any]:
    goal_id = _text(goal_id, "goal_id", max_length=160)
    init_db(cfg.sqlite_path)
    with get_connection(cfg.sqlite_path) as conn:
        exists = conn.execute("SELECT 1 FROM goals WHERE goal_id = ?", (goal_id,)).fetchone()
        if not exists:
            raise GoalError("ERROR_GOAL_NOT_FOUND", goal_id)
        rows = conn.execute(
            "SELECT payload_json FROM goal_events WHERE goal_id = ? ORDER BY seq ASC", (goal_id,)
        ).fetchall()
    return {"status": "GOAL_EVENTS_OK", "goal_id": goal_id, "events": [json.loads(row["payload_json"]) for row in rows]}


def create_goal(
    objective: str,
    *,
    active_vector: str = "general_task_execution",
    assumptions: list[str] | None = None,
    gaps: list[str] | None = None,
    variables: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    goal_id: str | None = None,
    created_by: str = "operator",
    make_current: bool = True,
    cfg: Settings = settings,
) -> dict[str, Any]:
    objective = _text(objective, "objective", max_length=MAX_OBJECTIVE_LENGTH)
    active_vector = _text(active_vector, "active_vector", max_length=MAX_VECTOR_LENGTH)
    assumptions = _string_list(assumptions, "assumptions")
    gaps = _string_list(gaps, "gaps")
    variables = _string_list(variables, "variables")
    if metadata is None:
        metadata = {}
    elif not isinstance(metadata, dict):
        raise GoalError("ERROR_GOAL_METADATA_INVALID", "metadata must be an object")
    else:
        metadata = dict(metadata)
    created_by = _text(created_by, "created_by", max_length=160)
    now = utc_now()
    resolved_id = _text(goal_id, "goal_id", max_length=160) if goal_id else stable_id("goal", objective, now, length=16)
    goal = {
        "schema_version": GOAL_SCHEMA_VERSION,
        "goal_id": resolved_id,
        "objective": objective,
        "active_vector": active_vector,
        "status": "ACTIVE",
        "assumptions": assumptions,
        "gaps": gaps,
        "variables": variables,
        "metadata": metadata,
        "is_current": bool(make_current),
        "created_at": now,
        "updated_at": now,
    }
    _validate_goal(goal)
    init_db(cfg.sqlite_path)
    conn = get_connection(cfg.sqlite_path)
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = _rows(conn)
        if any(item["goal_id"] == resolved_id for item in existing):
            raise GoalError("ERROR_GOAL_DUPLICATE_ID", resolved_id)
        if make_current:
            for item in existing:
                if item["is_current"]:
                    item["is_current"] = False
                    item["updated_at"] = now
                    _write_goal(conn, item)
        _write_goal(conn, goal)
        _append_event(conn, resolved_id, "GOAL_CREATED", {"created_by": created_by, "is_current": make_current})
        conn.execute("COMMIT")
        return {"status": "GOAL_CREATED", "goal": goal, "events": list_goal_events(resolved_id, cfg)["events"]}
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def update_goal(
    goal_id: str,
    *,
    status: str | None = None,
    active_vector: str | None = None,
    assumptions: list[str] | None = None,
    gaps: list[str] | None = None,
    variables: list[str] | None = None,
    make_current: bool | None = None,
    updated_by: str = "operator",
    cfg: Settings = settings,
) -> dict[str, Any]:
    goal_id = _text(goal_id, "goal_id", max_length=160)
    if status is None and active_vector is None and assumptions is None and gaps is None and variables is None and make_current is None:
        raise GoalError("ERROR_GOAL_UPDATE_EMPTY", "at least one goal field must be supplied")
    if status is not None and status not in GOAL_STATES:
        raise GoalError("ERROR_GOAL_STATUS_INVALID", status)
    updated_by = _text(updated_by, "updated_by", max_length=160)
    init_db(cfg.sqlite_path)
    conn = get_connection(cfg.sqlite_path)
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        goals = _rows(conn)
        goal = next((item for item in goals if item["goal_id"] == goal_id), None)
        if goal is None:
            raise GoalError("ERROR_GOAL_NOT_FOUND", goal_id)
        old_status = goal["status"]
        new_status = status or old_status
        if new_status not in GOAL_TRANSITIONS[old_status]:
            raise GoalError("ERROR_GOAL_TRANSITION", f"{old_status} -> {new_status}")
        if active_vector is not None:
            goal["active_vector"] = _text(active_vector, "active_vector", max_length=MAX_VECTOR_LENGTH)
        for label, value in (("assumptions", assumptions), ("gaps", gaps), ("variables", variables)):
            if value is not None:
                goal[label] = _string_list(value, label)
        goal["status"] = new_status
        if make_current is not None:
            goal["is_current"] = bool(make_current)
        elif new_status in TERMINAL_GOAL_STATES:
            goal["is_current"] = False
        goal["updated_at"] = utc_now()
        if goal["is_current"]:
            for item in goals:
                if item["goal_id"] != goal_id and item["is_current"]:
                    item["is_current"] = False
                    item["updated_at"] = goal["updated_at"]
                    _write_goal(conn, item)
        _write_goal(conn, goal)
        event_type = "GOAL_STATUS_CHANGED" if new_status != old_status else "GOAL_UPDATED"
        _append_event(conn, goal_id, event_type, {"updated_by": updated_by, "from_status": old_status, "to_status": new_status})
        conn.execute("COMMIT")
        return {"status": "GOAL_UPDATED", "goal": goal, "events": list_goal_events(goal_id, cfg)["events"]}
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

