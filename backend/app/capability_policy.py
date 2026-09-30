from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

POLICY_SCHEMA_VERSION = "mucaro-capability-policy/1.0"

KNOWN_CAPABILITIES = frozenset(
    {
        "filesystem.read",
        "filesystem.write",
        "network.public_http",
        "database.read",
        "database.write",
        "subprocess.execute",
        "model.invoke",
        "git.read",
        "git.mutate",
        "mcp.invoke",
        "subagent.spawn",
        "runtime.in_process",
        "runtime.local_worker",
        "runtime.container",
        "runtime.service",
        "runtime.federation_worker",
    }
)

RUNTIME_CAPABILITY = {
    "IN_PROCESS": "runtime.in_process",
    "LOCAL_WORKER": "runtime.local_worker",
    "CONTAINER": "runtime.container",
    "SERVICE": "runtime.service",
    "FEDERATION_WORKER": "runtime.federation_worker",
}


@dataclass(frozen=True)
class CapabilityPolicy:
    policy_id: str
    allowed: frozenset[str]
    denied: frozenset[str] = frozenset()

    @classmethod
    def build(
        cls,
        *,
        policy_id: str,
        allowed: Iterable[str] = (),
        denied: Iterable[str] = (),
    ) -> "CapabilityPolicy":
        return cls(policy_id=policy_id, allowed=frozenset(allowed), denied=frozenset(denied))


@dataclass(frozen=True)
class CapabilityDecision:
    status: str
    policy_id: str
    requested: tuple[str, ...]
    declared: tuple[str, ...]
    granted: tuple[str, ...]
    denied: tuple[str, ...]
    reasons: tuple[str, ...]

    @property
    def authorized(self) -> bool:
        return self.status == "AUTHORIZED"

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": POLICY_SCHEMA_VERSION,
            "status": self.status,
            "policy_id": self.policy_id,
            "requested": list(self.requested),
            "declared": list(self.declared),
            "granted": list(self.granted),
            "denied": list(self.denied),
            "reasons": list(self.reasons),
        }


def evaluate_capabilities(
    *,
    policy: CapabilityPolicy,
    declared_capabilities: Iterable[str],
    requested_capabilities: Iterable[str],
) -> CapabilityDecision:
    declared = frozenset(declared_capabilities)
    requested = frozenset(requested_capabilities)
    reasons: list[str] = []

    unknown_policy = (policy.allowed | policy.denied) - KNOWN_CAPABILITIES
    unknown_declared = declared - KNOWN_CAPABILITIES
    unknown_requested = requested - KNOWN_CAPABILITIES
    undeclared = requested - declared
    explicitly_denied = requested & policy.denied
    not_allowed = requested - policy.allowed

    if unknown_policy:
        reasons.append(f"policy references unknown capabilities: {','.join(sorted(unknown_policy))}")
    if unknown_declared:
        reasons.append(f"skill declares unknown capabilities: {','.join(sorted(unknown_declared))}")
    if unknown_requested:
        reasons.append(f"request contains unknown capabilities: {','.join(sorted(unknown_requested))}")
    if undeclared:
        reasons.append(f"request exceeds declared capabilities: {','.join(sorted(undeclared))}")
    if explicitly_denied:
        reasons.append(f"request includes explicitly denied capabilities: {','.join(sorted(explicitly_denied))}")
    if not_allowed:
        reasons.append(f"request exceeds policy allowlist: {','.join(sorted(not_allowed))}")

    denied = unknown_requested | undeclared | explicitly_denied | not_allowed
    granted = requested - denied if not reasons else frozenset()
    status = "AUTHORIZED" if not reasons else "DENIED"
    return CapabilityDecision(
        status=status,
        policy_id=policy.policy_id,
        requested=tuple(sorted(requested)),
        declared=tuple(sorted(declared)),
        granted=tuple(sorted(granted)),
        denied=tuple(sorted(denied)),
        reasons=tuple(reasons),
    )


def require_authorized(decision: CapabilityDecision) -> None:
    if not decision.authorized:
        raise PermissionError("; ".join(decision.reasons) or "capability request denied")
