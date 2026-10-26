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
    state: str = "PLAN_ONLY"
    certification: str = "BLOCKED"

def create_plan(profile: str, network: str, hardware: str, platforms: list[str]) -> ExecutionPlan:
    if profile not in PROFILES or network not in NETWORK or hardware not in HARDWARE:
        raise ValueError("unsupported execution profile, network policy or hardware state")
    if not platforms or len(platforms) != len(set(platforms)) or not set(platforms) <= PLATFORMS:
        raise ValueError("platforms must be unique, nonempty and supported")
    lanes = LANES if profile == "maximum" else LANES[:12] if profile == "extended" else LANES[:6]
    # Model-dependent lanes are designated for capability planning only;
    # actual test IDs must be reconciled with the authoritative T00-T19 catalog.
    model_lanes: tuple[str, ...] = ()
    return ExecutionPlan(profile, network, hardware, tuple(sorted(platforms)), lanes, model_lanes, (), "PLAN_ONLY", "BLOCKED")

def plan_json(plan: ExecutionPlan) -> str:
    return json.dumps(asdict(plan), sort_keys=True, indent=2) + "\n"
