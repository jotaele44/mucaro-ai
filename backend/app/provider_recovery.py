from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from .agent_checkpoints import CheckpointError, get_checkpoint, resume_checkpoint, transition_checkpoint
from .config import Settings, settings
from .model_router import (
    PROVIDER_INTERRUPTION_STATUSES,
    ModelProviderError,
    model_provider_names,
    normalize_model_provider,
)
from .model_routing_policy import ModelRoutingError, plan_model_route
from .utils import utc_now

FAILOVER_SCHEMA_VERSION = "mucaro-provider-failover/1.0"

__all__ = [
    "FAILOVER_SCHEMA_VERSION",
    "PROVIDER_INTERRUPTION_STATUSES",
    "ProviderRecoveryError",
    "execute_with_provider_checkpoint",
    "plan_provider_failover",
    "resume_with_failover",
    "resume_with_provider",
]


class ProviderRecoveryError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _stable_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _identity(checkpoint: dict[str, Any]) -> tuple[Any, Any, Any]:
    return (checkpoint["session_id"], checkpoint["run_id"], checkpoint["checkpoint_id"])


def _with_provider_event(checkpoint: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    # Provider events are append-only and bound to the sequence the transition will write.
    metadata = dict(checkpoint.get("metadata") or {})
    events = list(metadata.get("provider_events") or [])
    events.append({**event, "checkpoint_sequence": int(checkpoint.get("sequence", 0)) + 1, "at": utc_now()})
    metadata["provider_events"] = events
    return metadata


def execute_with_provider_checkpoint(
    checkpoint_id: str,
    *,
    provider: str,
    model: str | None,
    operation: Callable[[], dict[str, Any]],
    cfg: Settings = settings,
) -> dict[str, Any]:
    checkpoint = get_checkpoint(checkpoint_id, cfg)
    if checkpoint is None:
        raise ProviderRecoveryError("ERROR_CHECKPOINT_NOT_FOUND", checkpoint_id)
    if checkpoint.get("state") != "EXECUTING":
        raise ProviderRecoveryError(
            "ERROR_CHECKPOINT_NOT_EXECUTING", f"provider work requires EXECUTING state: {checkpoint.get('state')}"
        )
    identity = _identity(checkpoint)
    try:
        result = operation()
    except ModelProviderError as exc:
        if exc.status not in PROVIDER_INTERRUPTION_STATUSES:
            raise
        current = get_checkpoint(checkpoint_id, cfg)
        if current is None:
            raise ProviderRecoveryError("ERROR_CHECKPOINT_NOT_FOUND", checkpoint_id) from exc
        metadata = _with_provider_event(
            current,
            {"event": "PROVIDER_INTERRUPTED", "provider": provider, "model": model, "status": exc.status},
        )
        # One write: pause state, provider attribution, and the event land together.
        paused = transition_checkpoint(
            checkpoint_id,
            state="PAUSED_PROVIDER",
            pause_reason=f"{provider}:{exc.status}",
            annotations={"provider": provider, "model": model, "metadata": metadata},
            cfg=cfg,
        )
        if _identity(paused) != identity:
            raise ProviderRecoveryError("ERROR_PROVIDER_IDENTITY_DRIFT", "provider pause changed session/run/checkpoint identity")
        return {
            "status": "PAUSED_PROVIDER",
            "provider_error": exc.status,
            "checkpoint": paused,
        }
    current = get_checkpoint(checkpoint_id, cfg)
    assert current is not None
    if _identity(current) != identity:
        raise ProviderRecoveryError("ERROR_PROVIDER_IDENTITY_DRIFT", "provider execution changed session/run/checkpoint identity")
    return {"status": "PROVIDER_EXECUTION_OK", "result": result, "checkpoint": current}


def _resume(
    checkpoint_id: str,
    *,
    provider: str,
    model: str | None,
    allow_live: bool,
    allowed_providers: list[str] | None,
    task_type: str,
    selection: str,
    failover_decision_sha256: str | None,
    cfg: Settings,
) -> dict[str, Any]:
    before = get_checkpoint(checkpoint_id, cfg)
    if before is None:
        raise ProviderRecoveryError("ERROR_CHECKPOINT_NOT_FOUND", checkpoint_id)
    identity = _identity(before)
    # The resume target passes the same routing policy as a fresh run before any state changes.
    try:
        route = plan_model_route(
            task_type=task_type,
            preferred_provider=provider,
            preferred_model=model,
            allowed_providers=allowed_providers,
            allow_live=allow_live,
        )
    except ModelRoutingError as exc:
        raise ProviderRecoveryError(exc.status, exc.detail) from exc
    if route["status"] != "MODEL_ROUTE_READY":
        raise ProviderRecoveryError("ERROR_PROVIDER_ROUTE_BLOCKED", f"{provider}:{route['selection_reason']}")
    event: dict[str, Any] = {
        "event": "PROVIDER_RESUMED",
        "selection": selection,
        "from_provider": before.get("provider"),
        "to_provider": route["selected_provider"],
        "model": route["selected_model"],
        "route_sha256": route["route_sha256"],
    }
    if failover_decision_sha256 is not None:
        event["failover_decision_sha256"] = failover_decision_sha256
    try:
        resumed = resume_checkpoint(
            checkpoint_id,
            cfg,
            annotations={
                "provider": route["selected_provider"],
                "model": route["selected_model"],
                "metadata": _with_provider_event(before, event),
            },
        )
    except CheckpointError as exc:
        raise ProviderRecoveryError(exc.status, exc.detail) from exc
    if _identity(resumed) != identity:
        raise ProviderRecoveryError("ERROR_PROVIDER_IDENTITY_DRIFT", "provider switch changed canonical run identity")
    return resumed


def resume_with_provider(
    checkpoint_id: str,
    *,
    provider: str,
    model: str | None = None,
    allow_live: bool = False,
    allowed_providers: list[str] | None = None,
    task_type: str = "analysis",
    cfg: Settings = settings,
) -> dict[str, Any]:
    return _resume(
        checkpoint_id,
        provider=provider,
        model=model,
        allow_live=allow_live,
        allowed_providers=allowed_providers,
        task_type=task_type,
        selection="EXPLICIT",
        failover_decision_sha256=None,
        cfg=cfg,
    )


def plan_provider_failover(
    checkpoint: dict[str, Any],
    *,
    failover_chain: list[str],
    allow_live: bool = False,
    task_type: str = "analysis",
) -> dict[str, Any]:
    """Select the first policy-eligible provider from an explicit ordered chain without mutating state."""
    if not isinstance(failover_chain, list) or not failover_chain:
        raise ProviderRecoveryError("ERROR_PROVIDER_FAILOVER_CHAIN", "failover_chain must be a non-empty list")
    known = set(model_provider_names())
    chain: list[str] = []
    for raw in failover_chain:
        if not isinstance(raw, str):
            raise ProviderRecoveryError("ERROR_PROVIDER_FAILOVER_CHAIN", "failover_chain entries must be strings")
        provider = normalize_model_provider(raw)
        if provider not in known:
            raise ProviderRecoveryError("ERROR_UNSUPPORTED_MODEL_PROVIDER", provider)
        if provider not in chain:
            chain.append(provider)

    failed_provider = checkpoint.get("provider")
    candidates: list[dict[str, Any]] = []
    selected: dict[str, Any] | None = None
    for provider in chain:
        if provider == failed_provider:
            candidates.append(
                {"provider": provider, "live": None, "eligible": False, "reasons": ["FAILED_PROVIDER"], "route_sha256": None}
            )
            continue
        try:
            route = plan_model_route(
                task_type=task_type,
                preferred_provider=provider,
                allowed_providers=chain,
                allow_live=allow_live,
            )
        except ModelRoutingError as exc:
            raise ProviderRecoveryError(exc.status, exc.detail) from exc
        row = next(item for item in route["candidates"] if item["provider"] == provider)
        eligible = route["status"] == "MODEL_ROUTE_READY"
        candidate = {
            "provider": provider,
            "live": bool(row["live"]),
            "eligible": eligible,
            "reasons": list(row["reasons"]),
            "route_sha256": route["route_sha256"],
        }
        candidates.append(candidate)
        if eligible and selected is None:
            selected = candidate

    payload: dict[str, Any] = {
        "schema_version": FAILOVER_SCHEMA_VERSION,
        "status": "FAILOVER_READY" if selected else "FAILOVER_EXHAUSTED",
        "checkpoint_id": checkpoint.get("checkpoint_id"),
        "checkpoint_sequence": checkpoint.get("sequence"),
        "failed_provider": failed_provider,
        "failover_chain": chain,
        "allow_live": allow_live,
        "task_type": task_type,
        "candidates": candidates,
        "selected_provider": selected["provider"] if selected else None,
        "invariants": {
            "failed_provider_not_selected": selected is None or selected["provider"] != failed_provider,
            "selected_provider_in_chain": selected is None or selected["provider"] in chain,
            "no_implicit_live_escalation": selected is None or not selected["live"] or allow_live,
        },
    }
    payload["decision_sha256"] = _stable_hash(payload)
    return payload


def resume_with_failover(
    checkpoint_id: str,
    *,
    failover_chain: list[str],
    allow_live: bool = False,
    task_type: str = "analysis",
    cfg: Settings = settings,
) -> dict[str, Any]:
    checkpoint = get_checkpoint(checkpoint_id, cfg)
    if checkpoint is None:
        raise ProviderRecoveryError("ERROR_CHECKPOINT_NOT_FOUND", checkpoint_id)
    if checkpoint.get("state") != "PAUSED_PROVIDER":
        raise ProviderRecoveryError(
            "ERROR_CHECKPOINT_NOT_PROVIDER_PAUSED", f"failover requires PAUSED_PROVIDER state: {checkpoint.get('state')}"
        )
    decision = plan_provider_failover(
        checkpoint,
        failover_chain=failover_chain,
        allow_live=allow_live,
        task_type=task_type,
    )
    if decision["selected_provider"] is None:
        reasons = ", ".join(f"{row['provider']}:{'|'.join(row['reasons']) or 'NOT_READY'}" for row in decision["candidates"])
        raise ProviderRecoveryError("ERROR_PROVIDER_FAILOVER_EXHAUSTED", reasons)
    return _resume(
        checkpoint_id,
        provider=decision["selected_provider"],
        model=None,
        allow_live=allow_live,
        allowed_providers=decision["failover_chain"],
        task_type=task_type,
        selection="FAILOVER",
        failover_decision_sha256=decision["decision_sha256"],
        cfg=cfg,
    )
