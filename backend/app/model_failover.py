from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from .model_router import ModelProviderError
from .provider_recovery import PROVIDER_INTERRUPTION_STATUSES
from .utils import utc_now

FAILOVER_SCHEMA_VERSION = "mucaro-model-failover/1.0"
RETRY_STATUSES = frozenset({"ERROR_MODEL_PROVIDER_RATE_LIMIT", "ERROR_MODEL_PROVIDER_TIMEOUT"})
# Statuses that mean "this provider cannot answer right now; try the next one". Anything else
# (bad request, auth failure, unsupported provider) is a hard failure and is never masked by failover.
NEXT_PROVIDER_STATUSES = RETRY_STATUSES | frozenset(
    {
        "ERROR_MODEL_PROVIDER_UNAVAILABLE",
        "ERROR_MODEL_PROVIDER_MALFORMED_RESPONSE",
        "ERROR_MODEL_SDK_MISSING",
        "ERROR_MODEL_API_KEY_MISSING",
        "ERROR_LIVE_MODE_DISABLED",
    }
)


@dataclass(frozen=True)
class FailoverPolicy:
    max_attempts_per_provider: int = 2
    backoff_seconds: tuple[float, ...] = (0.5, 2.0)

    def __post_init__(self) -> None:
        if self.max_attempts_per_provider < 1:
            raise ValueError("max_attempts_per_provider must be >= 1")

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
    """Try providers in order with bounded retries; record every attempt.

    Rate limits and timeouts are retried on the same provider (bounded, with backoff), then the next
    provider is tried. Unavailable/malformed/unready providers go straight to the next provider.
    Hard failures stop the chain: failover must not hide a bad request or a credential problem.
    """
    policy = policy or FailoverPolicy()
    attempts: list[dict[str, Any]] = []
    if not chain:
        return {"schema_version": FAILOVER_SCHEMA_VERSION, "status": "FAILOVER_NO_PROVIDERS", "attempts": attempts, "interruption": False}

    last_status = ""
    last_detail = ""
    for entry in chain:
        provider, model = entry["provider"], entry.get("model")
        for attempt_in_provider in range(1, policy.max_attempts_per_provider + 1):
            started_at = utc_now()
            started = monotonic()
            try:
                result = call(provider, model)
            except ModelProviderError as exc:
                attempts.append(_record(len(attempts) + 1, provider, model, exc.status, exc.detail, started_at, started, monotonic))
                last_status, last_detail = exc.status, exc.detail
                # Part of the answer already reached the listener: neither a retry nor another provider may repeat it.
                recoverable = not getattr(exc, "partial_output", False)
                if (
                    recoverable
                    and exc.status in RETRY_STATUSES
                    and attempt_in_provider < policy.max_attempts_per_provider
                ):
                    sleep(policy.delay_before_retry(attempt_in_provider))
                    continue
                if recoverable and exc.status in NEXT_PROVIDER_STATUSES:
                    break
                return {
                    "schema_version": FAILOVER_SCHEMA_VERSION,
                    "status": "FAILOVER_HARD_FAILURE",
                    "error_status": exc.status,
                    "detail": exc.detail,
                    "attempts": attempts,
                    "interruption": False,
                }
            attempts.append(_record(len(attempts) + 1, provider, model, "OK", "", started_at, started, monotonic))
            return {
                "schema_version": FAILOVER_SCHEMA_VERSION,
                "status": "FAILOVER_OK",
                "provider": provider,
                "model": model,
                "result": result,
                "attempts": attempts,
                "failed_over": len({a["provider"] for a in attempts}) > 1,
            }
    failed = [a["status"] for a in attempts]
    return {
        "schema_version": FAILOVER_SCHEMA_VERSION,
        "status": "FAILOVER_EXHAUSTED",
        "error_status": last_status,
        "detail": last_detail,
        "attempts": attempts,
        # Only a pure outage pauses work; a malformed response from a healthy provider is an error to surface.
        "interruption": bool(failed) and all(status in PROVIDER_INTERRUPTION_STATUSES for status in failed),
    }


def _record(number: int, provider: str, model: str | None, status: str, detail: str, started_at: str, started: float, monotonic: Callable[[], float]) -> dict[str, Any]:
    return {
        "attempt": number,
        "provider": provider,
        "model": model,
        "status": status,
        "detail": detail[:500],
        "started_at": started_at,
        "duration_ms": int(max(0.0, monotonic() - started) * 1000),
    }
