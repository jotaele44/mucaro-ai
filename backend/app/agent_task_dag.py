from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema

SCHEMA_VERSION = "mucaro-agent-task-dag/1.0"
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
TASK_STATES = (
    "PENDING",
    "READY",
    "RUNNING",
    "AWAITING_APPROVAL",
    "AWAITING_INPUT",
    "BLOCKED",
    "FAILED",
    "PASSED",
    "SKIPPED",
    "SUPERSEDED",
)
TERMINAL_TASK_STATES = frozenset({"FAILED", "PASSED", "SKIPPED", "SUPERSEDED"})
NONPASS_DEPENDENCY_STATES = frozenset({"BLOCKED", "FAILED", "SKIPPED", "SUPERSEDED"})
MAX_TASKS = 256
MAX_EDGES = 4096
MAX_TITLE_CHARS = 512
MAX_DESCRIPTION_CHARS = 4000

_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "PENDING": frozenset({"SKIPPED", "SUPERSEDED"}),
    "READY": frozenset({"RUNNING", "AWAITING_APPROVAL", "AWAITING_INPUT", "BLOCKED", "SKIPPED", "SUPERSEDED"}),
    "RUNNING": frozenset({"PASSED", "FAILED", "AWAITING_APPROVAL", "AWAITING_INPUT", "BLOCKED"}),
    "AWAITING_APPROVAL": frozenset({"READY", "RUNNING", "BLOCKED", "SKIPPED", "SUPERSEDED"}),
    "AWAITING_INPUT": frozenset({"READY", "RUNNING", "BLOCKED", "SKIPPED", "SUPERSEDED"}),
    "BLOCKED": frozenset({"READY", "SKIPPED", "SUPERSEDED"}),
    "FAILED": frozenset(),
    "PASSED": frozenset(),
    "SKIPPED": frozenset(),
    "SUPERSEDED": frozenset(),
}


class TaskDagError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "schemas" / "agent_task_dag.schema.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TaskDagError("ERROR_TASK_DAG_SCHEMA_UNAVAILABLE", f"{path}: {exc}") from exc


def _stable_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _definition(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": task["id"],
        "title": task["title"],
        "description": task["description"],
        "depends_on": sorted(task["depends_on"]),
        "metadata": deepcopy(task["metadata"]),
    }


def _normalize_definitions(definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(definitions, list) or not definitions or len(definitions) > MAX_TASKS:
        raise TaskDagError("ERROR_TASK_DAG_TASK_COUNT", f"task definitions must contain 1..{MAX_TASKS} tasks")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    allowed = {"id", "title", "description", "depends_on", "metadata"}
    for index, raw in enumerate(definitions):
        if not isinstance(raw, dict):
            raise TaskDagError("ERROR_TASK_DAG_DEFINITION", f"task {index} must be an object")
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise TaskDagError("ERROR_TASK_DAG_DEFINITION_FIELD", f"task {index} has unknown fields: {','.join(unknown)}")
        task_id = raw.get("id")
        title = raw.get("title")
        description = raw.get("description", "")
        depends_on = raw.get("depends_on", [])
        metadata = raw.get("metadata", {})
        if not isinstance(task_id, str) or not TASK_ID_PATTERN.fullmatch(task_id):
            raise TaskDagError("ERROR_TASK_DAG_ID", f"task {index} has invalid id")
        if task_id in seen:
            raise TaskDagError("ERROR_TASK_DAG_DUPLICATE_ID", task_id)
        seen.add(task_id)
        if not isinstance(title, str) or not title.strip() or len(title) > MAX_TITLE_CHARS:
            raise TaskDagError("ERROR_TASK_DAG_TITLE", task_id)
        if not isinstance(description, str) or len(description) > MAX_DESCRIPTION_CHARS:
            raise TaskDagError("ERROR_TASK_DAG_DESCRIPTION", task_id)
        if not isinstance(depends_on, list) or not all(isinstance(item, str) and TASK_ID_PATTERN.fullmatch(item) for item in depends_on):
            raise TaskDagError("ERROR_TASK_DAG_DEPENDENCIES", task_id)
        if len(depends_on) != len(set(depends_on)):
            raise TaskDagError("ERROR_TASK_DAG_DUPLICATE_DEPENDENCY", task_id)
        if task_id in depends_on:
            raise TaskDagError("ERROR_TASK_DAG_SELF_DEPENDENCY", task_id)
        if not isinstance(metadata, dict):
            raise TaskDagError("ERROR_TASK_DAG_METADATA", task_id)
        normalized.append(
            {
                "id": task_id,
                "title": title.strip(),
                "description": description,
                "depends_on": sorted(depends_on),
                "metadata": deepcopy(metadata),
            }
        )

    ids = {task["id"] for task in normalized}
    for task in normalized:
        missing = sorted(set(task["depends_on"]) - ids)
        if missing:
            raise TaskDagError("ERROR_TASK_DAG_MISSING_DEPENDENCY", f"{task['id']}:{','.join(missing)}")
    edge_count = sum(len(task["depends_on"]) for task in normalized)
    if edge_count > MAX_EDGES:
        raise TaskDagError("ERROR_TASK_DAG_EDGE_COUNT", f"edge count {edge_count} exceeds {MAX_EDGES}")
    return normalized


def _topological_order(definitions: list[dict[str, Any]]) -> list[str]:
    dependencies = {task["id"]: set(task["depends_on"]) for task in definitions}
    children: dict[str, set[str]] = {task["id"]: set() for task in definitions}
    for task_id, deps in dependencies.items():
        for dependency in deps:
            children[dependency].add(task_id)

    ready = sorted(task_id for task_id, deps in dependencies.items() if not deps)
    order: list[str] = []
    while ready:
        task_id = ready.pop(0)
        order.append(task_id)
        for child in sorted(children[task_id]):
            dependencies[child].discard(task_id)
            if not dependencies[child] and child not in order and child not in ready:
                ready.append(child)
        ready.sort()

    if len(order) != len(definitions):
        residue = sorted(set(dependencies) - set(order))
        raise TaskDagError("ERROR_TASK_DAG_CYCLE", ",".join(residue))
    return order


def _state_counts(tasks: list[dict[str, Any]]) -> dict[str, int]:
    return {state: sum(task.get("state") == state for task in tasks) for state in TASK_STATES}


def _semantic_summary(tasks: list[dict[str, Any]], topological_order: list[str]) -> tuple[list[str], dict[str, int]]:
    by_id = {task["id"]: task for task in tasks}
    ready_task_ids = [
        task_id
        for task_id in topological_order
        if by_id[task_id]["state"] == "READY"
    ]
    return ready_task_ids, _state_counts(tasks)


def _dag_metadata(dag: dict[str, Any]) -> dict[str, Any]:
    return {key: deepcopy(value) for key, value in dag.items() if key != "tasks"}


def task_dag_metadata(dag: dict[str, Any]) -> dict[str, Any]:
    validate_task_dag(dag)
    return _dag_metadata(dag)


def build_task_dag(
    definitions: list[dict[str, Any]],
    *,
    created_at: str | None = None,
) -> dict[str, Any]:
    normalized = _normalize_definitions(definitions)
    topological_order = _topological_order(normalized)
    created = created_at or _utc_now()
    by_id = {task["id"]: task for task in normalized}
    tasks: list[dict[str, Any]] = []
    for task_id in topological_order:
        definition = by_id[task_id]
        tasks.append(
            {
                **deepcopy(definition),
                "state": "READY" if not definition["depends_on"] else "PENDING",
                "attempt_count": 0,
                "evidence": [],
                "artifacts": [],
                "blocked_reason": None,
                "created_at": created,
                "started_at": None,
                "completed_at": None,
            }
        )

    ready_task_ids, state_counts = _semantic_summary(tasks, topological_order)
    definition_sha256 = _stable_hash([_definition(task) for task in sorted(tasks, key=lambda item: item["id"])])
    dag = {
        "schema_version": SCHEMA_VERSION,
        "definition_sha256": definition_sha256,
        "created_at": created,
        "task_count": len(tasks),
        "edge_count": sum(len(task["depends_on"]) for task in tasks),
        "tasks": tasks,
        "topological_order": topological_order,
        "ready_task_ids": ready_task_ids,
        "state_counts": state_counts,
        "invariants": {
            "unique_task_ids": True,
            "dependencies_exist": True,
            "acyclic": True,
            "row_accounting_closed": sum(state_counts.values()) == len(tasks),
            "ready_dependencies_satisfied": all(not by_id[task_id]["depends_on"] for task_id in ready_task_ids),
        },
    }
    validate_task_dag(dag)
    return dag


def validate_task_dag(dag: dict[str, Any]) -> None:
    if not isinstance(dag, dict):
        raise TaskDagError("ERROR_TASK_DAG_SHAPE", "task DAG must be an object")
    errors = sorted(jsonschema.Draft202012Validator(_schema()).iter_errors(dag), key=lambda error: list(error.absolute_path))
    if errors:
        where = "/".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise TaskDagError("ERROR_TASK_DAG_SCHEMA", f"{where}: {errors[0].message}")

    tasks = dag["tasks"]
    definitions = _normalize_definitions([_definition(task) for task in tasks])
    order = _topological_order(definitions)
    ids = [task["id"] for task in tasks]
    if len(ids) != len(set(ids)):
        raise TaskDagError("ERROR_TASK_DAG_DUPLICATE_ID", "stored task ids are not unique")
    if set(ids) != set(order):
        raise TaskDagError("ERROR_TASK_DAG_TASK_SET", "stored task set does not match topology")
    if dag["topological_order"] != order:
        raise TaskDagError("ERROR_TASK_DAG_TOPOLOGY_DRIFT", "stored topological order is not canonical")
    expected_definition_sha256 = _stable_hash(
        [_definition(task) for task in sorted(tasks, key=lambda item: item["id"])]
    )
    if dag["definition_sha256"] != expected_definition_sha256:
        raise TaskDagError("ERROR_TASK_DAG_DEFINITION_DRIFT", "definition hash does not match stored tasks")
    if dag["task_count"] != len(tasks):
        raise TaskDagError("ERROR_TASK_DAG_COUNT", "task_count does not close")
    edge_count = sum(len(task["depends_on"]) for task in tasks)
    if dag["edge_count"] != edge_count:
        raise TaskDagError("ERROR_TASK_DAG_EDGE_COUNT", "edge_count does not close")

    by_id = {task["id"]: task for task in tasks}
    for task in tasks:
        state = task["state"]
        if state not in TASK_STATES:
            raise TaskDagError("ERROR_TASK_DAG_STATE", f"{task['id']}:{state}")
        if state == "PASSED" and not task["evidence"]:
            raise TaskDagError("ERROR_TASK_DAG_PASS_WITHOUT_EVIDENCE", task["id"])
        if state in TERMINAL_TASK_STATES and not task["completed_at"]:
            raise TaskDagError("ERROR_TASK_DAG_TERMINAL_TIME", task["id"])
        if state not in TERMINAL_TASK_STATES and task["completed_at"] is not None:
            raise TaskDagError("ERROR_TASK_DAG_NONTERMINAL_TIME", task["id"])
        if state == "READY" and not all(by_id[dependency]["state"] == "PASSED" for dependency in task["depends_on"]):
            raise TaskDagError("ERROR_TASK_DAG_READY_DEPENDENCY", task["id"])

    ready, counts = _semantic_summary(tasks, order)
    if dag["ready_task_ids"] != ready:
        raise TaskDagError("ERROR_TASK_DAG_READY_SET", "ready_task_ids does not match task states")
    if dag["state_counts"] != counts or sum(counts.values()) != len(tasks):
        raise TaskDagError("ERROR_TASK_DAG_STATE_COUNTS", "state accounting does not close")


def _materialize(dag: dict[str, Any], tasks: list[dict[str, Any]]) -> dict[str, Any]:
    result = deepcopy(dag)
    by_id = {task["id"]: task for task in tasks}

    changed = True
    while changed:
        changed = False
        for task_id in result["topological_order"]:
            task = by_id[task_id]
            if task["state"] not in {"PENDING", "READY"}:
                continue
            dependency_states = [(dependency, by_id[dependency]["state"]) for dependency in task["depends_on"]]
            blocking = sorted(
                dependency
                for dependency, state in dependency_states
                if state in NONPASS_DEPENDENCY_STATES
            )
            if blocking:
                if task["state"] != "BLOCKED" or task.get("blocked_reason") != f"dependency_not_passed:{','.join(blocking)}":
                    task["state"] = "BLOCKED"
                    task["blocked_reason"] = f"dependency_not_passed:{','.join(blocking)}"
                    changed = True
                continue
            if all(state == "PASSED" for _, state in dependency_states):
                if task["state"] != "READY":
                    task["state"] = "READY"
                    task["blocked_reason"] = None
                    changed = True
            elif task["state"] == "READY":
                task["state"] = "PENDING"
                changed = True

    result["tasks"] = [by_id[task_id] for task_id in result["topological_order"]]
    ready, counts = _semantic_summary(result["tasks"], result["topological_order"])
    result["ready_task_ids"] = ready
    result["state_counts"] = counts
    result["invariants"] = {
        "unique_task_ids": len(by_id) == len(result["tasks"]),
        "dependencies_exist": all(
            dependency in by_id
            for task in result["tasks"]
            for dependency in task["depends_on"]
        ),
        "acyclic": True,
        "row_accounting_closed": sum(counts.values()) == len(result["tasks"]),
        "ready_dependencies_satisfied": all(
            all(by_id[dependency]["state"] == "PASSED" for dependency in by_id[task_id]["depends_on"])
            for task_id in ready
        ),
    }
    validate_task_dag(result)
    return result


def transition_task_dag(
    dag: dict[str, Any],
    *,
    task_id: str,
    state: str,
    evidence: list[dict[str, Any]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    blocked_reason: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    validate_task_dag(dag)
    if state not in TASK_STATES:
        raise TaskDagError("ERROR_TASK_DAG_STATE", state)
    tasks = deepcopy(dag["tasks"])
    by_id = {task["id"]: task for task in tasks}
    if task_id not in by_id:
        raise TaskDagError("ERROR_TASK_DAG_TASK_NOT_FOUND", task_id)

    task = by_id[task_id]
    current = task["state"]
    if state not in _ALLOWED_TRANSITIONS[current]:
        raise TaskDagError("ERROR_TASK_DAG_TRANSITION", f"{task_id}:{current}->{state}")
    timestamp = now or _utc_now()

    if evidence is not None and (not isinstance(evidence, list) or not all(isinstance(item, dict) for item in evidence)):
        raise TaskDagError("ERROR_TASK_DAG_EVIDENCE", task_id)
    if artifacts is not None and (not isinstance(artifacts, list) or not all(isinstance(item, dict) for item in artifacts)):
        raise TaskDagError("ERROR_TASK_DAG_ARTIFACTS", task_id)
    if state in {"PASSED", "FAILED"} and not evidence:
        raise TaskDagError("ERROR_TASK_DAG_TERMINAL_EVIDENCE", task_id)
    if state in {"BLOCKED", "SKIPPED", "SUPERSEDED"} and not (blocked_reason or "").strip():
        raise TaskDagError("ERROR_TASK_DAG_REASON_REQUIRED", task_id)
    if state == "READY":
        unmet = [dependency for dependency in task["depends_on"] if by_id[dependency]["state"] != "PASSED"]
        if unmet:
            raise TaskDagError("ERROR_TASK_DAG_READY_DEPENDENCY", f"{task_id}:{','.join(sorted(unmet))}")

    task["state"] = state
    if state == "RUNNING":
        task["attempt_count"] = int(task["attempt_count"]) + 1
        task["started_at"] = task["started_at"] or timestamp
        task["blocked_reason"] = None
    elif state == "READY":
        task["blocked_reason"] = None
    elif state in {"BLOCKED", "SKIPPED", "SUPERSEDED"}:
        task["blocked_reason"] = str(blocked_reason).strip()
    if evidence:
        task["evidence"] = [*task["evidence"], *deepcopy(evidence)]
    if artifacts:
        task["artifacts"] = [*task["artifacts"], *deepcopy(artifacts)]
    if state in TERMINAL_TASK_STATES:
        task["completed_at"] = timestamp

    return _materialize(dag, list(by_id.values()))


def reconstruct_task_dag(
    *,
    metadata: dict[str, Any],
    pending_tasks: list[dict[str, Any]],
    completed_tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(metadata, dict) or metadata.get("schema_version") != SCHEMA_VERSION:
        raise TaskDagError("ERROR_TASK_DAG_METADATA", "checkpoint task DAG metadata is missing or invalid")
    tasks = [deepcopy(task) for task in [*pending_tasks, *completed_tasks]]
    by_id = {task.get("id"): task for task in tasks if isinstance(task, dict)}
    order = metadata.get("topological_order")
    if not isinstance(order, list) or set(order) != set(by_id):
        raise TaskDagError("ERROR_TASK_DAG_TASK_SET", "checkpoint task set does not match stored topology")
    dag = {**deepcopy(metadata), "tasks": [by_id[task_id] for task_id in order]}
    validate_task_dag(dag)
    return dag


def split_task_dag(dag: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    validate_task_dag(dag)
    pending = [deepcopy(task) for task in dag["tasks"] if task["state"] not in TERMINAL_TASK_STATES]
    completed = [deepcopy(task) for task in dag["tasks"] if task["state"] in TERMINAL_TASK_STATES]
    return pending, completed, _dag_metadata(dag)
