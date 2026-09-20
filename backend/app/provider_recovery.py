from __future__ import annotations

from typing import Any, Callable

from .agent_checkpoints import CheckpointError, get_checkpoint, resume_checkpoint, transition_checkpoint
from .config import Settings, settings
from .model_router import ModelProviderError

PROVIDER_INTERRUPTION_STATUSES = {
    "ERROR_LIVE_MODE_DISABLED",
    "ERROR_MODEL_API_KEY_MISSING",
    "ERROR_MODEL_SDK_MISSING",
    "ERROR_MODEL_PROVIDER_UNAVAILABLE",
    "ERROR_MODEL_PROVIDER_RATE_LIMIT",
    "ERROR_MODEL_PROVIDER_TIMEOUT",
}


class ProviderRecoveryError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


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
    identity = (checkpoint["session_id"], checkpoint["run_id"], checkpoint["checkpoint_id"])
    try:
        result = operation()
    except ModelProviderError as exc:
        if exc.status not in PROVIDER_INTERRUPTION_STATUSES:
            raise
        paused = transition_checkpoint(
            checkpoint_id,
            state="PAUSED_PROVIDER",
            pause_reason=f"{provider}:{exc.status}",
            cfg=cfg,
        )
        paused["provider"] = provider
        paused["model"] = model
        # Persist provider/model annotations without changing canonical identity.
        from .db import upsert_payload
        upsert_payload("agent_checkpoints", "checkpoint_id", paused, cfg.sqlite_path)
        return {
            "status": "PAUSED_PROVIDER",
            "provider_error": exc.status,
            "checkpoint": paused,
        }
    current = get_checkpoint(checkpoint_id, cfg)
    assert current is not None
    if (current["session_id"], current["run_id"], current["checkpoint_id"]) != identity:
        raise ProviderRecoveryError("ERROR_PROVIDER_IDENTITY_DRIFT", "provider execution changed session/run/checkpoint identity")
    return {"status": "PROVIDER_EXECUTION_OK", "result": result, "checkpoint": current}


def resume_with_provider(
    checkpoint_id: str,
    *,
    provider: str,
    model: str | None = None,
    cfg: Settings = settings,
) -> dict[str, Any]:
    before = get_checkpoint(checkpoint_id, cfg)
    if before is None:
        raise ProviderRecoveryError("ERROR_CHECKPOINT_NOT_FOUND", checkpoint_id)
    identity = (before["session_id"], before["run_id"], before["checkpoint_id"])
    try:
        resumed = resume_checkpoint(checkpoint_id, cfg)
    except CheckpointError as exc:
        raise ProviderRecoveryError(exc.status, exc.detail) from exc
    resumed["provider"] = provider
    resumed["model"] = model
    from .db import upsert_payload
    upsert_payload("agent_checkpoints", "checkpoint_id", resumed, cfg.sqlite_path)
    after_identity = (resumed["session_id"], resumed["run_id"], resumed["checkpoint_id"])
    if after_identity != identity:
        raise ProviderRecoveryError("ERROR_PROVIDER_IDENTITY_DRIFT", "provider switch changed canonical run identity")
    return resumed
