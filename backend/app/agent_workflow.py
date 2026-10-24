"""Validation and deterministic scheduling for bounded agent action graphs."""

from __future__ import annotations

from typing import Any

DEFAULT_AGENT_PARALLELISM = 2
MAX_AGENT_PARALLELISM = 4
DEFAULT_AGENT_TURNS = 4
MAX_AGENT_TURNS = 8


class AgentWorkflowError(ValueError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def normalize_agent_parallelism(value: Any = None) -> int:
    """Validate the bounded concurrency policy used by autonomous plans."""
    if value is None:
        return DEFAULT_AGENT_PARALLELISM
    if isinstance(value, bool) or not isinstance(value, int):
        raise AgentWorkflowError("ERROR_AGENT_PARALLELISM", "max_parallel_actions must be an integer")
    if value < 1 or value > MAX_AGENT_PARALLELISM:
        raise AgentWorkflowError(
            "ERROR_AGENT_PARALLELISM",
            f"max_parallel_actions must be between 1 and {MAX_AGENT_PARALLELISM}",
        )
    return value


def normalize_agent_turns(value: Any = None) -> int:
    """Validate the bounded number of provider-native tool rounds."""
    if value is None:
        return DEFAULT_AGENT_TURNS
    if isinstance(value, bool) or not isinstance(value, int):
        raise AgentWorkflowError("ERROR_AGENT_TURNS", "max_agent_turns must be an integer")
    if value < 1 or value > MAX_AGENT_TURNS:
        raise AgentWorkflowError("ERROR_AGENT_TURNS", f"max_agent_turns must be between 1 and {MAX_AGENT_TURNS}")
    return value


def normalize_agent_actions(actions: list[dict[str, Any]], *, max_actions: int) -> list[dict[str, Any]]:
    """Return a validated topological action list with stable step identities.

    Provider-native plans may omit IDs and dependencies. Local plans receive
    deterministic IDs. Explicit dependencies are retained and never inferred
    from names or list position.
    """
    if len(actions) > max_actions:
        raise AgentWorkflowError("ERROR_AGENT_WORKFLOW_LIMIT", f"agent workflow exceeds the bounded limit of {max_actions} actions")
    prepared: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            raise AgentWorkflowError("ERROR_AGENT_WORKFLOW_ACTION", f"action {index + 1} is not an object")
        step_id = action.get("id") or f"agent-step-{index + 1}"
        if not isinstance(step_id, str) or not step_id.strip():
            raise AgentWorkflowError("ERROR_AGENT_WORKFLOW_STEP_ID", f"action {index + 1} has no usable step id")
        if step_id in seen:
            raise AgentWorkflowError("ERROR_AGENT_WORKFLOW_DUPLICATE_STEP", step_id)
        seen.add(step_id)
        dependencies = action.get("depends_on", [])
        if not isinstance(dependencies, list) or any(not isinstance(dep, str) or not dep.strip() for dep in dependencies):
            raise AgentWorkflowError("ERROR_AGENT_WORKFLOW_DEPENDENCIES", f"step {step_id} dependencies must be non-empty strings")
        if len(set(dependencies)) != len(dependencies):
            raise AgentWorkflowError("ERROR_AGENT_WORKFLOW_DUPLICATE_DEPENDENCY", step_id)
        prepared.append({**action, "id": step_id, "depends_on": list(dependencies)})

    ids = {action["id"] for action in prepared}
    for action in prepared:
        unknown = sorted(set(action["depends_on"]) - ids)
        if unknown:
            raise AgentWorkflowError("ERROR_AGENT_WORKFLOW_UNKNOWN_DEPENDENCY", f"step {action['id']} references {unknown}")

    by_id = {action["id"]: action for action in prepared}
    indegree = {step_id: len(action["depends_on"]) for step_id, action in by_id.items()}
    dependents = {step_id: [] for step_id in by_id}
    for action in prepared:
        for dependency in action["depends_on"]:
            dependents[dependency].append(action["id"])
    order = {action["id"]: index for index, action in enumerate(prepared)}
    ready = sorted((step_id for step_id, count in indegree.items() if count == 0), key=order.__getitem__)
    topological: list[str] = []
    while ready:
        current = ready.pop(0)
        topological.append(current)
        for dependent in sorted(dependents[current], key=order.__getitem__):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
                ready.sort(key=order.__getitem__)
    if len(topological) != len(prepared):
        raise AgentWorkflowError("ERROR_AGENT_WORKFLOW_CYCLE", "agent workflow dependencies contain a cycle")
    return [by_id[step_id] for step_id in topological]
