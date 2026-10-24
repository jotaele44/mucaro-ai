from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import Settings, settings
from .db import get_connection, init_db
from .utils import stable_id, utc_now

TASK_SCHEMA_VERSION = "mucaro-agent-task/1.0"
TASK_STATES = ("TODO", "IN_PROGRESS", "BLOCKED", "DONE", "FAILED", "CANCELLED")
TASK_PRIORITIES = ("low", "normal", "high", "urgent")
TERMINAL_TASK_STATES = frozenset({"DONE", "FAILED", "CANCELLED"})
TASK_TRANSITIONS: dict[str, frozenset[str]] = {
    "TODO": frozenset({"TODO", "IN_PROGRESS", "BLOCKED", "DONE", "CANCELLED"}),
    "IN_PROGRESS": frozenset({"IN_PROGRESS", "BLOCKED", "DONE", "FAILED", "CANCELLED"}),
    "BLOCKED": frozenset({"TODO", "IN_PROGRESS", "CANCELLED"}),
    "DONE": frozenset(),
    "FAILED": frozenset({"TODO", "CANCELLED"}),
    "CANCELLED": frozenset(),
}


class TaskError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _validate_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 160:
        raise TaskError(f"ERROR_TASK_{label.upper()}_INVALID", f"{label} must be a non-empty string of at most 160 characters")
    return value.strip()


def _validate_title(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskError("ERROR_TASK_TITLE_REQUIRED", "title is required")
    if len(value.strip()) > 500:
        raise TaskError("ERROR_TASK_TITLE_TOO_LONG", "title must be at most 500 characters")
    return value.strip()


def _normalize_dependencies(dependencies: list[str] | tuple[str, ...] | None, *, task_id: str) -> list[str]:
    if dependencies is None:
        return []
    if not isinstance(dependencies, (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in dependencies):
        raise TaskError("ERROR_TASK_DEPENDENCIES_INVALID", "depends_on must contain non-empty task IDs")
    normalized = sorted({item.strip() for item in dependencies})
    if task_id in normalized:
        raise TaskError("ERROR_TASK_SELF_DEPENDENCY", task_id)
    return normalized


def _validate_payload(task: dict[str, Any]) -> None:
    _validate_id(task.get("task_id"), "task_id")
    _validate_title(task.get("title"))
    if task.get("schema_version") != TASK_SCHEMA_VERSION:
        raise TaskError("ERROR_TASK_SCHEMA", "unsupported task schema version")
    if not isinstance(task.get("description"), str):
        raise TaskError("ERROR_TASK_DESCRIPTION_INVALID", "description must be a string")
    if task.get("owner") is not None and not isinstance(task.get("owner"), str):
        raise TaskError("ERROR_TASK_OWNER_INVALID", "owner must be a string or null")
    if not isinstance(task.get("metadata"), dict):
        raise TaskError("ERROR_TASK_METADATA_INVALID", "metadata must be an object")
    if task.get("status") not in TASK_STATES:
        raise TaskError("ERROR_TASK_STATE_INVALID", str(task.get("status")))
    if task.get("priority") not in TASK_PRIORITIES:
        raise TaskError("ERROR_TASK_PRIORITY_INVALID", str(task.get("priority")))
    if not isinstance(task.get("depends_on"), list) or any(not isinstance(item, str) or not item for item in task["depends_on"]):
        raise TaskError("ERROR_TASK_DEPENDENCIES_INVALID", "depends_on must be a list of task IDs")
    if not isinstance(task.get("sequence"), int) or task["sequence"] < 0:
        raise TaskError("ERROR_TASK_SEQUENCE_INVALID", "sequence must be a non-negative integer")
    if task["status"] == "BLOCKED" and not str(task.get("blocked_reason") or "").strip():
        raise TaskError("ERROR_TASK_BLOCKED_REASON_REQUIRED", "BLOCKED tasks require blocked_reason")


def _validate_graph(tasks: list[dict[str, Any]]) -> None:
    by_id = {str(task.get("task_id")): task for task in tasks}
    if len(by_id) != len(tasks):
        raise TaskError("ERROR_TASK_DUPLICATE_ID", "task IDs must be unique")
    for task in tasks:
        _validate_payload(task)
        for dependency in task["depends_on"]:
            if dependency not in by_id:
                raise TaskError("ERROR_TASK_UNKNOWN_DEPENDENCY", f"{task['task_id']} depends on {dependency}")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str, trail: tuple[str, ...] = ()) -> None:
        if task_id in visiting:
            cycle = (*trail, task_id)
            raise TaskError("ERROR_TASK_DEPENDENCY_CYCLE", " -> ".join(cycle))
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in by_id[task_id]["depends_on"]:
            visit(dependency, (*trail, task_id))
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in sorted(by_id):
        visit(task_id)


def _decode_rows(rows: list[Any]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for row in rows:
        try:
            task = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise TaskError("ERROR_TASK_CORRUPT", "stored task payload is not valid JSON") from exc
        if not isinstance(task, dict):
            raise TaskError("ERROR_TASK_CORRUPT", "stored task payload is not an object")
        _validate_payload(task)
        tasks.append(task)
    _validate_graph(tasks)
    return tasks


def list_tasks(cfg: Settings = settings, *, status: str | None = None) -> list[dict[str, Any]]:
    if status is not None and status not in TASK_STATES:
        raise TaskError("ERROR_TASK_STATE_INVALID", status)
    init_db(cfg.sqlite_path)
    with get_connection(cfg.sqlite_path) as conn:
        rows = conn.execute("SELECT payload_json FROM agent_tasks ORDER BY rowid ASC").fetchall()
    tasks = _decode_rows(rows)
    if status is not None:
        tasks = [task for task in tasks if task["status"] == status]
    return tasks


def get_task(task_id: str, cfg: Settings = settings) -> dict[str, Any] | None:
    task_id = _validate_id(task_id, "task_id")
    init_db(cfg.sqlite_path)
    with get_connection(cfg.sqlite_path) as conn:
        all_rows = conn.execute("SELECT payload_json FROM agent_tasks ORDER BY rowid ASC").fetchall()
    tasks = _decode_rows(all_rows)
    return next((task for task in tasks if task["task_id"] == task_id), None)


def task_summary(cfg: Settings = settings) -> dict[str, Any]:
    tasks = list_tasks(cfg)
    counts = {state: sum(task["status"] == state for task in tasks) for state in TASK_STATES}
    ready = [task["task_id"] for task in tasks if task["status"] == "TODO" and all(
        next(item for item in tasks if item["task_id"] == dependency)["status"] == "DONE"
        for dependency in task["depends_on"]
    )]
    return {
        "status": "TASK_SUMMARY_OK",
        "task_count": len(tasks),
        "state_counts": counts,
        "ready_task_ids": ready,
        "dependency_graph_valid": True,
    }


def create_task(
    title: str,
    *,
    description: str = "",
    priority: str = "normal",
    depends_on: list[str] | None = None,
    owner: str | None = None,
    task_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    cfg: Settings = settings,
) -> dict[str, Any]:
    title = _validate_title(title)
    if priority not in TASK_PRIORITIES:
        raise TaskError("ERROR_TASK_PRIORITY_INVALID", priority)
    task_id = _validate_id(task_id, "task_id") if task_id is not None else stable_id("task", title, utc_now(), length=16)
    if metadata is not None and not isinstance(metadata, dict):
        raise TaskError("ERROR_TASK_METADATA_INVALID", "metadata must be an object")
    now = utc_now()
    task = {
        "schema_version": TASK_SCHEMA_VERSION,
        "task_id": task_id,
        "title": title,
        "description": str(description or "").strip(),
        "status": "TODO",
        "priority": priority,
        "depends_on": _normalize_dependencies(depends_on, task_id=task_id),
        "owner": owner.strip() if isinstance(owner, str) and owner.strip() else None,
        "blocked_reason": None,
        "created_at": now,
        "updated_at": now,
        "sequence": 0,
        "metadata": dict(metadata or {}),
    }
    _validate_payload(task)
    init_db(cfg.sqlite_path)
    conn = get_connection(cfg.sqlite_path)
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute("SELECT payload_json FROM agent_tasks ORDER BY rowid ASC").fetchall()
        existing = _decode_rows(rows)
        if any(item["task_id"] == task_id for item in existing):
            raise TaskError("ERROR_TASK_DUPLICATE_ID", task_id)
        _validate_graph([*existing, task])
        conn.execute(
            "INSERT INTO agent_tasks (task_id, payload_json) VALUES (?, ?)",
            (task_id, json.dumps(task, ensure_ascii=False, sort_keys=True)),
        )
        conn.execute("COMMIT")
        return task
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def update_task(
    task_id: str,
    *,
    status: str | None = None,
    blocked_reason: str | None = None,
    depends_on: list[str] | None = None,
    expected_sequence: int | None = None,
    cfg: Settings = settings,
) -> dict[str, Any]:
    task_id = _validate_id(task_id, "task_id")
    if status is None and depends_on is None:
        raise TaskError("ERROR_TASK_STATE_REQUIRED", "status is required")
    if status is not None and status not in TASK_STATES:
        raise TaskError("ERROR_TASK_STATE_INVALID", status)
    if expected_sequence is not None and (not isinstance(expected_sequence, int) or expected_sequence < 0):
        raise TaskError("ERROR_TASK_SEQUENCE_INVALID", "expected_sequence must be a non-negative integer")
    init_db(cfg.sqlite_path)
    conn = get_connection(cfg.sqlite_path)
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute("SELECT task_id, payload_json FROM agent_tasks ORDER BY rowid ASC").fetchall()
        tasks = _decode_rows(rows)
        current = next((task for task in tasks if task["task_id"] == task_id), None)
        if current is None:
            raise TaskError("ERROR_TASK_NOT_FOUND", task_id)
        if expected_sequence is not None and current["sequence"] != expected_sequence:
            raise TaskError("ERROR_TASK_CONFLICT", f"expected sequence {expected_sequence}, found {current['sequence']}")
        updated = dict(current)
        target = status or current["status"]
        if target not in TASK_TRANSITIONS[current["status"]]:
            raise TaskError("ERROR_TASK_TRANSITION", f"illegal transition {current['status']} -> {target}")
        if depends_on is not None and current["status"] != "TODO":
            raise TaskError("ERROR_TASK_DEPENDENCY_MUTATION", "dependencies can only change while a task is TODO")
        if depends_on is not None:
            updated["depends_on"] = _normalize_dependencies(depends_on, task_id=task_id)
        if target in {"IN_PROGRESS", "DONE"}:
            by_id = {task["task_id"]: task for task in tasks}
            for dependency in updated["depends_on"]:
                if by_id[dependency]["status"] != "DONE":
                    raise TaskError("ERROR_TASK_DEPENDENCIES_NOT_READY", f"{task_id} depends on unfinished task {dependency}")
        if target == "BLOCKED" and not str(blocked_reason or current.get("blocked_reason") or "").strip():
            raise TaskError("ERROR_TASK_BLOCKED_REASON_REQUIRED", "BLOCKED tasks require blocked_reason")
        updated["status"] = target
        updated["blocked_reason"] = blocked_reason.strip() if isinstance(blocked_reason, str) and blocked_reason.strip() else (current.get("blocked_reason") if target == "BLOCKED" else None)
        updated["sequence"] = current["sequence"] + 1
        updated["updated_at"] = utc_now()
        _validate_graph([updated if task["task_id"] == task_id else task for task in tasks])
        conn.execute(
            "UPDATE agent_tasks SET payload_json = ? WHERE task_id = ?",
            (json.dumps(updated, ensure_ascii=False, sort_keys=True), task_id),
        )
        conn.execute("COMMIT")
        return updated
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
