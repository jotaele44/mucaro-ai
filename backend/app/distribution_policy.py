from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

LOCAL_DISTRIBUTION_CHANNEL = "local"
MAC_APP_STORE_DISTRIBUTION_CHANNEL = "mac_app_store"
SUPPORTED_DISTRIBUTION_CHANNELS = frozenset(
    {LOCAL_DISTRIBUTION_CHANNEL, MAC_APP_STORE_DISTRIBUTION_CHANNEL}
)

APP_STORE_ALLOWED_RUNTIMES = frozenset({"IN_PROCESS"})
APP_STORE_ALLOWED_SKILL_IDS = frozenset({"mucaro/intelligence-plane-health"})
APP_STORE_DENIED_CAPABILITIES = frozenset(
    {
        "subprocess.execute",
        "runtime.local_worker",
        "runtime.container",
        "runtime.service",
        "runtime.federation_worker",
    }
)


class DistributionPolicyError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class DistributionRuntimeDecision:
    status: str
    distribution_channel: str
    runtime: str
    skill_id: str | None
    denied_capabilities: tuple[str, ...]
    reasons: tuple[str, ...]

    @property
    def authorized(self) -> bool:
        return self.status == "AUTHORIZED"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "distribution_channel": self.distribution_channel,
            "runtime": self.runtime,
            "skill_id": self.skill_id,
            "denied_capabilities": list(self.denied_capabilities),
            "reasons": list(self.reasons),
        }


def evaluate_distribution_runtime_policy(
    *,
    distribution_channel: str,
    runtime: str,
    granted_capabilities: Iterable[str] = (),
    skill_id: str | None = None,
) -> DistributionRuntimeDecision:
    granted = frozenset(granted_capabilities)
    reasons: list[str] = []
    denied: frozenset[str] = frozenset()

    if distribution_channel not in SUPPORTED_DISTRIBUTION_CHANNELS:
        reasons.append(f"unsupported distribution channel: {distribution_channel}")
    elif distribution_channel == MAC_APP_STORE_DISTRIBUTION_CHANNEL:
        if runtime not in APP_STORE_ALLOWED_RUNTIMES:
            reasons.append(f"runtime disabled for Mac App Store distribution: {runtime}")
        elif skill_id not in APP_STORE_ALLOWED_SKILL_IDS:
            reasons.append(f"skill not reviewed for Mac App Store distribution: {skill_id or 'missing'}")
        denied = granted & APP_STORE_DENIED_CAPABILITIES
        if denied:
            reasons.append(
                "capabilities disabled for Mac App Store distribution: "
                + ",".join(sorted(denied))
            )

    return DistributionRuntimeDecision(
        status="DENIED" if reasons else "AUTHORIZED",
        distribution_channel=distribution_channel,
        runtime=runtime,
        skill_id=skill_id,
        denied_capabilities=tuple(sorted(denied)),
        reasons=tuple(reasons),
    )


def require_distribution_runtime_authorized(
    *,
    distribution_channel: str,
    runtime: str,
    granted_capabilities: Iterable[str] = (),
    skill_id: str | None = None,
) -> DistributionRuntimeDecision:
    decision = evaluate_distribution_runtime_policy(
        distribution_channel=distribution_channel,
        runtime=runtime,
        granted_capabilities=granted_capabilities,
        skill_id=skill_id,
    )
    if not decision.authorized:
        raise DistributionPolicyError(
            "ERROR_DISTRIBUTION_RUNTIME_POLICY",
            "; ".join(decision.reasons),
        )
    return decision
