from __future__ import annotations

import hashlib\nimport json\nfrom typing import Any, Callable

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


def _attempt_id(checkpoint: dict[str, Any], operation_key: str) -> str:\n    payload = {\n        "session_id": checkpoint["session_id"],\n        "run_id": checkpoint["run_id"],\n        "checkpoint_id": checkpoint["checkpoint_id"],\n        "operation_key": operation_key,\n    }\n    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")\n    return hashlib.sha256(raw).hexdigest()[:24]\n\n\nclass ProviderRecoveryError(RuntimeError):
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
            provider=provider,
            model=model,
            cfg=cfg,
        )
        return {
            "status": "PAUSED_PROVIDER",
            "provider_error": exc.status,
            "checkpoint": paused,
        }
    current = get_checkpoint(checkpoint_id, cfg)
    if current is None:
        raise ProviderRecoveryError("ERROR_PROVIDER_CHECKPOINT_MISSING", "checkpoint disappeared during provider execution")
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
        resumed = resume_checkpoint(checkpoint_id, cfg, provider=provider, model=model)
    except CheckpointError as exc:
        raise ProviderRecoveryError(exc.status, exc.detail) from exc
    after_identity = (resumed["session_id"], resumed["run_id"], resumed["checkpoint_id"])
    if after_identity != identity:
        raise ProviderRecoveryError("ERROR_PROVIDER_IDENTITY_DRIFT", "provider switch changed canonical run identity")
    return resumed
