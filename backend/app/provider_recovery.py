from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from .agent_checkpoints import CheckpointError, get_checkpoint, record_provider_attempt, resume_checkpoint, transition_checkpoint
from .config import Settings, settings
from .model_router import ModelProviderError
from .utils import utc_now

PROVIDER_INTERRUPTION_STATUSES = {
    "ERROR_LIVE_MODE_DISABLED",
    "ERROR_MODEL_API_KEY_MISSING",
    "ERROR_MODEL_SDK_MISSING",
    "ERROR_MODEL_PROVIDER_UNAVAILABLE",
    "ERROR_MODEL_PROVIDER_RATE_LIMIT",
    "ERROR_MODEL_PROVIDER_TIMEOUT",
}
# The provider may or may not have acted on the request: retrying is allowed, calling it ACCEPTED is not.
ATTEMPT_INTERRUPTED_UNKNOWN = "INTERRUPTED_UNKNOWN"
ATTEMPT_FAILED = "FAILED"


class ProviderRecoveryError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def provider_attempt_id(checkpoint: dict[str, Any], operation_key: str) -> str:
    """Identity of one logical operation in one run. Provider and model are deliberately not part of it:
    retrying on another provider after an outage is the same operation, not a new one."""
    payload = {
        "session_id": checkpoint["session_id"],
        "run_id": checkpoint["run_id"],
        "checkpoint_id": checkpoint["checkpoint_id"],
        "operation_key": operation_key,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def _stored_result(result: Any) -> Any:
    try:
        return json.loads(json.dumps(result, sort_keys=True, ensure_ascii=False))
    except (TypeError, ValueError):
        return {"unserializable_result_type": type(result).__name__}


def _record(checkpoint_id: str, attempt_id: str, record: dict[str, Any], cfg: Settings, **flags: bool) -> dict[str, Any]:
    try:
        return record_provider_attempt(checkpoint_id, attempt_id=attempt_id, record=record, cfg=cfg, **flags)
    except CheckpointError as exc:
        raise ProviderRecoveryError(exc.status, exc.detail) from exc


def execute_with_provider_checkpoint(
    checkpoint_id: str,
    *,
    provider: str,
    model: str | None,
    operation: Callable[[], dict[str, Any]],
    operation_key: str | None = None,
    reclaim_started: bool = False,
    cfg: Settings = settings,
) -> dict[str, Any]:
    """Run one provider call against a checkpoint.

    With ``operation_key`` the call is replay-safe: an operation already ACCEPTED for this run is not run
    again (its stored result is returned), and an interrupted one is recorded as INTERRUPTED_UNKNOWN."""
    checkpoint = get_checkpoint(checkpoint_id, cfg)
    if checkpoint is None:
        raise ProviderRecoveryError("ERROR_CHECKPOINT_NOT_FOUND", checkpoint_id)
    identity = (checkpoint["session_id"], checkpoint["run_id"], checkpoint["checkpoint_id"])
    attempt_id: str | None = None
    record: dict[str, Any] = {}
    if operation_key is not None:
        if not operation_key.strip():
            raise ProviderRecoveryError("ERROR_PROVIDER_OPERATION_KEY", "operation_key must not be empty")
        attempt_id = provider_attempt_id(checkpoint, operation_key)
        started = {"attempt_id": attempt_id, "operation_key": operation_key, "provider": provider, "model": model, "started_at": utc_now()}
        try:
            claimed = record_provider_attempt(checkpoint_id, attempt_id=attempt_id, record=started, claim=True, reclaim_started=reclaim_started, cfg=cfg)
        except CheckpointError as exc:
            if exc.status != "ERROR_CHECKPOINT_ATTEMPT_ACCEPTED":
                raise ProviderRecoveryError(exc.status, exc.detail) from exc
            accepted = get_checkpoint(checkpoint_id, cfg)
            prior = ((accepted or {}).get("metadata") or {}).get("provider_attempts", {}).get(attempt_id, {})
            return {"status": "PROVIDER_EXECUTION_ALREADY_ACCEPTED", "attempt_id": attempt_id, "result": prior.get("result"), "checkpoint": accepted}
        record = dict(claimed["metadata"]["provider_attempts"][attempt_id])
    try:
        result = operation()
    except ModelProviderError as exc:
        interrupted = exc.status in PROVIDER_INTERRUPTION_STATUSES
        if attempt_id:
            outcome = ATTEMPT_INTERRUPTED_UNKNOWN if interrupted else ATTEMPT_FAILED
            _record(checkpoint_id, attempt_id, {**record, "state": outcome, "provider_error": exc.status, "finished_at": utc_now()}, cfg)
        if not interrupted:
            raise
        paused = transition_checkpoint(
            checkpoint_id,
            state="PAUSED_PROVIDER",
            pause_reason=f"{provider}:{exc.status}",
            provider=provider,
            model=model,
            cfg=cfg,
        )
        paused_result: dict[str, Any] = {"status": "PAUSED_PROVIDER", "provider_error": exc.status, "checkpoint": paused}
        if attempt_id:
            paused_result["attempt_id"] = attempt_id
        return paused_result
    except BaseException as exc:
        # A crash or bug during the call: whether the provider acted is unknown, so it is never ACCEPTED.
        if attempt_id:
            _record(checkpoint_id, attempt_id, {**record, "state": ATTEMPT_INTERRUPTED_UNKNOWN, "provider_error": type(exc).__name__, "finished_at": utc_now()}, cfg)
        raise
    if attempt_id:
        current = _record(checkpoint_id, attempt_id, {**record, "state": "ACCEPTED", "result": _stored_result(result), "finished_at": utc_now()}, cfg)
    else:
        current = get_checkpoint(checkpoint_id, cfg)
    if current is None:
        raise ProviderRecoveryError("ERROR_PROVIDER_CHECKPOINT_MISSING", "checkpoint disappeared during provider execution")
    if (current["session_id"], current["run_id"], current["checkpoint_id"]) != identity:
        raise ProviderRecoveryError("ERROR_PROVIDER_IDENTITY_DRIFT", "provider execution changed session/run/checkpoint identity")
    ok: dict[str, Any] = {"status": "PROVIDER_EXECUTION_OK", "result": result, "checkpoint": current}
    if attempt_id:
        ok["attempt_id"] = attempt_id
    return ok


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
