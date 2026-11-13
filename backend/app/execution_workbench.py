"""Fail-closed, model-independent workbench planning contract.

Planning is side-effect-free. This module does not run tests, issue approvals,
certify releases, deploy artifacts or promote skills.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import json

LANES = tuple(f"T{i:02d}" for i in range(20))
PLATFORMS = frozenset({"linux", "macos", "windows"})
PROFILES = frozenset({"blockers", "extended", "maximum"})
NETWORK = frozenset({"isolated", "offline"})
HARDWARE = frozenset({"available", "unknown", "unavailable"})

@dataclass(frozen=True)
class ExecutionPlan:
    profile: str
    network: str
    hardware: str
    platforms: tuple[str, ...]
    lanes: tuple[str, ...]
    model_lanes: tuple[str, ...]
    blocked_lanes: tuple[str, ...]
    lane_status: tuple[tuple[str, str], ...] = ()
    catalog_status: str = "UNBOUND"
    catalog_ref: str | None = None
    state: str = "PLAN_ONLY"
    certification: str = "BLOCKED"

def create_plan(profile: str, network: str, hardware: str, platforms: list[str]) -> ExecutionPlan:
    if profile not in PROFILES or network not in NETWORK or hardware not in HARDWARE:
        raise ValueError("unsupported execution profile, network policy or hardware state")
    if not platforms or len(platforms) != len(set(platforms)) or not set(platforms) <= PLATFORMS:
        raise ValueError("platforms must be unique, nonempty and supported")
    lanes = LANES if profile == "maximum" else LANES[:12] if profile == "extended" else LANES[:6]

    # T00-T19 are retained as stable planning labels from the operator profile,
    # but the repository currently contains no authoritative catalog binding
    # those labels to executable tests. Do not guess model-dependent lanes or
    # report them as merely unattempted: every selected lane fails closed until
    # the catalog is explicitly bound.
    model_lanes: tuple[str, ...] = ()
    blocked_lanes = lanes
    lane_status = tuple((lane, "UNRESOLVED_CATALOG") for lane in lanes)
    return ExecutionPlan(
        profile=profile,
        network=network,
        hardware=hardware,
        platforms=tuple(sorted(platforms)),
        lanes=lanes,
        model_lanes=model_lanes,
        blocked_lanes=blocked_lanes,
        lane_status=lane_status,
        catalog_status="UNBOUND",
        catalog_ref=None,
        state="PLAN_ONLY",
        certification="BLOCKED",
    )

def plan_json(plan: ExecutionPlan) -> str:
    return json.dumps(asdict(plan), sort_keys=True, indent=2) + "\n"
