from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from .contracts import validate_contract
from .model_router import ModelProviderError
from .provider_recovery import PROVIDER_INTERRUPTION_STATUSES
from .utils import utc_now

FAILOVER_SCHEMA_VERSION = "mucaro-model-failover/1.0"
RETRY_STATUSES = frozenset({"ERROR_MODEL_PROVIDER_RATE_LIMIT", "ERROR_MODEL_PROVIDER_TIMEOUT"})
NEXT_PROVIDER_STATUSES = RETRY_STATUSES | frozenset(
    {
        "ERROR_MODEL_PROVIDER_UNAVAILABLE",
        "ERROR_MODEL_PROVIDER_MALFORMED_RESPONSE",
        "ERROR_MODEL_SDK_MISSING",
        "ERROR_MODEL_API_KEY_MISSING",
        "ERROR_LIVE_MODE_DISABLED",
        "ERROR_LOCAL_MODEL_UNAVAILABLE",
        "ERROR_LOCAL_MODEL_NOT_INSTALLED",
        "ERROR_LOCAL_MODEL_RESPONSE_INVALID",
        "ERROR_LOCAL_MODEL_EMPTY_RESPONSE",
    }
)


def _validated_receipt(payload: dict[str, Any]) -> dict[str, Any]:
    return validate_contract("model_failover", payload)


@dataclass(frozen=True)
class FailoverPolicy:
    max_attempts_per_provider: int = 2
    backoff_seconds: tuple[float, ...] = (0.5, 2.0)

    def __post_init__(self) -> None:
        if self.max_attempts_per_provider < 1:
            raise ValueError("max_attempts_per_provider must be >= 1")
        if any(delay < 0 for delay in self.backoff_seconds):
            raise ValueError("backoff_seconds must be non-negative")

    def delay_before_retry(self, completed_attempts: int) -> float:
        if not self.backoff_seconds:
            return 0.0
        return self.backoff_seconds[min(completed_attempts - 1, len(self.backoff_seconds) - 1)]


def run_with_failover(
    chain: list[dict[str, Any]],
    call: Callable[[str, str | None], dict[str, Any]],
    *,
    policy: FailoverPolicy | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Try providers in order with bounded retries and a complete attempt receipt."""
    policy = policy or FailoverPolicy()
    attempts: list[dict[str, Any]] = []
    if not chain:
        return _validated_receipt({
            "schema_version": FAILOVER_SCHEMA_VERSION,
            "status": "FAILOVER_NO_PROVIDERS",
            "attempts": attempts,
            "interruption": False,
        })

    last_status = ""
    last_detail = ""
    for entry in chain:
        provider = str(entry.get("provider") or "").strip()
        if not provider:
            raise ValueError("failover provider is required")
        model = entry.get("model")
        if model is not None:
            model = str(model)
        for attempt_in_provider in range(1, policy.max_attempts_per_provider + 1):
            started_at = utc_now()
            started = monotonic()
            try:
                result = call(provider, model)
            except ModelProviderError as exc:
                attempts.append(
                    _record(
                        len(attempts) + 1,
                        provider,
                        model,
                        exc.status,
                        exc.detail,
                        started_at,
                        started,
                        monotonic,
                    )
                )
                last_status, last_detail = exc.status, exc.detail
                if exc.status in RETRY_STATUSES and attempt_in_provider < policy.max_attempts_per_provider:
                    sleep(policy.delay_before_retry(attempt_in_provider))
                    continue
                if exc.status in NEXT_PROVIDER_STATUSES:
                    break
                return _validated_receipt({
                    "schema_version": FAILOVER_SCHEMA_VERSION,
                    "status": "FAILOVER_HARD_FAILURE",
                    "error_status": exc.status,
                    "detail": exc.detail,
                    "attempts": attempts,
                    "interruption": False,
                })

            attempts.append(
                _record(len(attempts) + 1, provider, model, "OK", "", started_at, started, monotonic)
            )
            return _validated_receipt({
                "schema_version": FAILOVER_SCHEMA_VERSION,
                "status": "FAILOVER_OK",
                "provider": provider,
                "model": model,
                "result": result,
                "attempts": attempts,
                "failed_over": len({attempt["provider"] for attempt in attempts}) > 1,
            })

    failed = [attempt["status"] for attempt in attempts]
    return _validated_receipt({
        "schema_version": FAILOVER_SCHEMA_VERSION,
        "status": "FAILOVER_EXHAUSTED",
        "error_status": last_status,
        "detail": last_detail,
        "attempts": attempts,
        "interruption": bool(failed) and all(status in PROVIDER_INTERRUPTION_STATUSES for status in failed),
    })


def _record(
    number: int,
    provider: str,
    model: str | None,
    status: str,
    detail: str,
    started_at: str,
    started: float,
    monotonic: Callable[[], float],
) -> dict[str, Any]:
    return {
        "attempt": number,
        "provider": provider,
        "model": model,
        "status": status,
        "detail": detail[:500],
        "started_at": started_at,
        "duration_ms": int(max(0.0, monotonic() - started) * 1000),
    }
