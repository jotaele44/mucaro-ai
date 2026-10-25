"""Deterministic fail-closed Auto Mode planner. No command execution or network access."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping

class AutoModeError(ValueError):
    pass

@dataclass(frozen=True)
class Vector:
    id: str
    dependencies: tuple[str, ...] = ()
    isolation_key: str = ""
    requires_authorization: bool = False

@dataclass(frozen=True)
class Plan:
    ready: tuple[str, ...]
    blocked: tuple[str, ...]
    completed: tuple[str, ...]
    terminal: bool

def schedule(vectors: tuple[Vector, ...], states: Mapping[str, str], authorized: frozenset[str] = frozenset()) -> Plan:
    """Compute one deterministic wave. Unknown or failed dependencies never run."""
    by_id = {v.id: v for v in vectors}
    if len(by_id) != len(vectors) or any(not v.id.strip() for v in vectors):
        raise AutoModeError("DUPLICATE_OR_EMPTY_VECTOR")
    for v in vectors:
        if v.id in v.dependencies or any(d not in by_id for d in v.dependencies):
            raise AutoModeError("INVALID_DEPENDENCY")
    # Kahn cycle check; preserve the full graph even when some nodes are already complete.
    pending = set(by_id)
    visited = set()
    while pending:
        wave = {n for n in pending if set(by_id[n].dependencies) <= visited}
        if not wave:
            raise AutoModeError("DEPENDENCY_CYCLE")
        visited.update(wave)
        pending -= wave
    if any(key not in by_id for key in states) or any(value not in {"PENDING", "PASS", "FAILED", "BLOCKED"} for value in states.values()):
        raise AutoModeError("INVALID_STATE")
    if not authorized <= by_id.keys():
        raise AutoModeError("UNKNOWN_AUTHORIZATION")
    completed = tuple(sorted(n for n in by_id if states.get(n, "PENDING") == "PASS"))
    ready = []
    blocked = []
    active_keys = set()
    for name in sorted(by_id):
        v = by_id[name]
        if states.get(name, "PENDING") != "PENDING":
            continue
        if any(states.get(d, "PENDING") != "PASS" for d in v.dependencies):
            blocked.append(name)
        elif v.requires_authorization and name not in authorized:
            blocked.append(name)
        elif v.isolation_key and v.isolation_key in active_keys:
            blocked.append(name)
        else:
            ready.append(name)
            if v.isolation_key:
                active_keys.add(v.isolation_key)
    return Plan(tuple(ready), tuple(blocked), completed, len(completed) == len(by_id))
