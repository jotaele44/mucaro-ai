from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .capability_policy import KNOWN_CAPABILITIES, CapabilityPolicy, evaluate_capabilities
from .config import Settings, settings
from .contracts import validate_contract
from .distribution_policy import (
    APP_STORE_DENIED_CAPABILITIES,
    MAC_APP_STORE_DISTRIBUTION_CHANNEL,
    SUPPORTED_DISTRIBUTION_CHANNELS,
)
from .permissions import DirectoryPermissionError, check_directory_permission

PERMISSION_DECISION_SCHEMA_VERSION = "mucaro-permission-decision/1.0"

ALLOW = "ALLOW"
ASK = "ASK"
DENY = "DENY"

# Capabilities whose execution changes the world outside the process. A request that only
# *plans* one of these is never authorization to run it (flagpole invariant 4).
SIDE_EFFECTING_CAPABILITIES = frozenset(
    {
        "filesystem.write",
        "database.write",
        "network.public_http",
        "subprocess.execute",
        "git.mutate",
        "mcp.invoke",
        "subagent.spawn",
        "runtime.local_worker",
        "runtime.container",
        "runtime.service",
        "runtime.federation_worker",
    }
)
FILESYSTEM_CAPABILITIES = frozenset({"filesystem.read", "filesystem.write"})
# The git capabilities are implemented by spawning the git CLI, so they inherit subprocess restrictions.
SUBPROCESS_BACKED_CAPABILITIES = frozenset({"git.read", "git.mutate"})
ORIGINS = frozenset({"operator", "planning", "agent"})


class PermissionEngineError(PermissionError):
    def __init__(self, decision: "PermissionDecision") -> None:
        super().__init__("; ".join(decision.reasons) or decision.decision)
        self.decision = decision
        self.status = "ERROR_PERMISSION_DENIED" if decision.decision == DENY else "ERROR_PERMISSION_APPROVAL_REQUIRED"
        self.detail = str(self)


@dataclass(frozen=True)
class PermissionRequest:
    capability: str
    subject: str
    target: str | None = None
    origin: str = "agent"
    approved: bool = False
    declared: frozenset[str] | None = None  # None = subject declares exactly what it requests

    def __post_init__(self) -> None:
        if self.declared is not None and not isinstance(self.declared, frozenset):
            object.__setattr__(self, "declared", frozenset(self.declared))


@dataclass(frozen=True)
class PermissionDecision:
    decision: str
    capability: str
    subject: str
    target: str | None
    origin: str
    reasons: tuple[str, ...]

    @property
    def allowed(self) -> bool:
        return self.decision == ALLOW

    def as_dict(self) -> dict[str, object]:
        payload = {
            "schema_version": PERMISSION_DECISION_SCHEMA_VERSION,
            "decision": self.decision,
            "capability": self.capability,
            "subject": self.subject,
            "target": self.target,
            "origin": self.origin,
            "reasons": list(self.reasons),
        }
        return validate_contract("permission_decision", payload)


def default_policy() -> CapabilityPolicy:
    """Every known capability is allowlisted; the gate is approval, grants and distribution."""
    return CapabilityPolicy.build(policy_id="mucaro-default-local", allowed=KNOWN_CAPABILITIES)


def _nearest_existing(path: Path) -> Path:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def _decision(request: PermissionRequest, verdict: str, reasons: Iterable[str]) -> PermissionDecision:
    return PermissionDecision(
        decision=verdict,
        capability=request.capability,
        subject=request.subject,
        target=request.target,
        origin=request.origin,
        reasons=tuple(reasons),
    )


def evaluate(
    request: PermissionRequest,
    *,
    cfg: Settings = settings,
    policy: CapabilityPolicy | None = None,
    distribution_channel: str | None = None,
) -> PermissionDecision:
    """Single fail-closed permission decision. Deny beats ask beats allow.

    ``distribution_channel`` overrides ``cfg.distribution_channel`` for callers (workers) that are
    handed a channel explicitly rather than a Settings object.
    """
    policy = policy or default_policy()
    channel = distribution_channel if distribution_channel is not None else cfg.distribution_channel
    if request.origin not in ORIGINS:
        return _decision(request, DENY, [f"unknown request origin: {request.origin}"])
    if request.capability not in KNOWN_CAPABILITIES:
        return _decision(request, DENY, [f"unknown capability: {request.capability}"])

    declared = request.declared if request.declared is not None else frozenset({request.capability})
    capability_decision = evaluate_capabilities(
        policy=policy,
        declared_capabilities=declared,
        requested_capabilities=[request.capability],
    )
    if not capability_decision.authorized:
        return _decision(request, DENY, capability_decision.reasons)

    if channel not in SUPPORTED_DISTRIBUTION_CHANNELS:
        return _decision(request, DENY, [f"unsupported distribution channel: {channel}"])
    effective = {request.capability} | ({"subprocess.execute"} if request.capability in SUBPROCESS_BACKED_CAPABILITIES else set())
    if channel == MAC_APP_STORE_DISTRIBUTION_CHANNEL and effective & APP_STORE_DENIED_CAPABILITIES:
        return _decision(request, DENY, [f"capability disabled for Mac App Store distribution: {request.capability}"])

    if request.capability in FILESYSTEM_CAPABILITIES:
        if not request.target:
            return _decision(request, DENY, ["filesystem request requires a target path"])
        # resolve() collapses '..' and symlinks before the grant check, so neither escapes a grant.
        resolved = Path(request.target).expanduser().resolve()
        try:
            grant = check_directory_permission(_nearest_existing(resolved), cfg)
        except DirectoryPermissionError as exc:
            return _decision(request, DENY, [exc.detail])
        if grant["status"] != "PERMISSION_OK":
            return _decision(request, DENY, [str(grant.get("detail", "directory permission required"))])

    if request.capability in SIDE_EFFECTING_CAPABILITIES:
        if request.origin == "planning":
            # Even approved=True does not help: approval must arrive as a fresh execution request.
            return _decision(request, ASK, ["planning never authorizes execution; explicit approval required"])
        if not (request.origin == "operator" or request.approved):
            return _decision(request, ASK, [f"approval required for side-effecting capability: {request.capability}"])

    return _decision(request, ALLOW, [])


def require(
    request: PermissionRequest,
    *,
    cfg: Settings = settings,
    policy: CapabilityPolicy | None = None,
    distribution_channel: str | None = None,
) -> PermissionDecision:
    decision = evaluate(request, cfg=cfg, policy=policy, distribution_channel=distribution_channel)
    if not decision.allowed:
        raise PermissionEngineError(decision)
    return decision
