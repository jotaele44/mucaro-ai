from __future__ import annotations

import pytest

from backend.app.contracts import validate_contract
from backend.app.model_failover import FailoverPolicy, run_with_failover
from backend.app.model_router import ModelProviderError
from backend.app.model_routing_policy import plan_model_route

RATE = "ERROR_MODEL_PROVIDER_RATE_LIMIT"
TIMEOUT = "ERROR_MODEL_PROVIDER_TIMEOUT"
DOWN = "ERROR_MODEL_PROVIDER_UNAVAILABLE"
MALFORMED = "ERROR_MODEL_PROVIDER_MALFORMED_RESPONSE"
HARD = "ERROR_MODEL_PROVIDER_CALL_FAILED"
LOCAL_DOWN = "ERROR_LOCAL_MODEL_UNAVAILABLE"
CHAIN = [{"provider": "primary", "model": "m1"}, {"provider": "secondary", "model": "m2"}]


def scripted(script: dict[str, list]):
    calls: list[str] = []

    def call(provider: str, model: str | None) -> dict:
        calls.append(provider)
        step = script[provider].pop(0)
        if isinstance(step, str):
            raise ModelProviderError(step, f"{provider} {step}")
        return step

    return call, calls


def run(script, chain=CHAIN, policy=None):
    call, calls = scripted(script)
    sleeps: list[float] = []
    out = run_with_failover(chain, call, policy=policy or FailoverPolicy(), sleep=sleeps.append)
    return out, calls, sleeps


def test_first_provider_success_records_single_attempt() -> None:
    out, calls, sleeps = run({"primary": [{"answer": "a"}], "secondary": []})
    assert out["status"] == "FAILOVER_OK"
    assert out["provider"] == "primary"
    assert out["failed_over"] is False
    assert calls == ["primary"]
    assert sleeps == []
    validate_contract("model_failover", {key: value for key, value in out.items() if key != "result"})


def test_rate_limit_retries_with_backoff_then_succeeds_on_same_provider() -> None:
    out, calls, sleeps = run({"primary": [RATE, {"answer": "a"}], "secondary": []})
    assert out["provider"] == "primary"
    assert calls == ["primary", "primary"]
    assert sleeps == [0.5]
    assert [attempt["status"] for attempt in out["attempts"]] == [RATE, "OK"]


def test_retries_are_bounded_then_failover_to_next_provider() -> None:
    out, calls, sleeps = run({"primary": [TIMEOUT, TIMEOUT], "secondary": [{"answer": "b"}]})
    assert out["provider"] == "secondary"
    assert out["failed_over"] is True
    assert calls == ["primary", "primary", "secondary"]
    assert sleeps == [0.5]


def test_unavailable_skips_retry_and_moves_to_next_provider() -> None:
    out, calls, sleeps = run({"primary": [DOWN], "secondary": [{"answer": "b"}]})
    assert calls == ["primary", "secondary"]
    assert sleeps == []
    assert out["provider"] == "secondary"


def test_exhausted_provider_interruptions_are_classified_for_checkpoint_pause() -> None:
    out, calls, _ = run({"primary": [LOCAL_DOWN], "secondary": [DOWN]})
    assert calls == ["primary", "secondary"]
    assert out["status"] == "FAILOVER_EXHAUSTED"
    assert out["interruption"] is True


def test_malformed_response_can_failover_but_is_not_an_interruption() -> None:
    out, calls, _ = run({"primary": [DOWN], "secondary": [MALFORMED]})
    assert calls == ["primary", "secondary"]
    assert out["status"] == "FAILOVER_EXHAUSTED"
    assert out["interruption"] is False


def test_hard_failure_stops_chain_without_masking() -> None:
    out, calls, _ = run({"primary": [HARD], "secondary": [{"answer": "b"}]})
    assert out["status"] == "FAILOVER_HARD_FAILURE"
    assert out["error_status"] == HARD
    assert calls == ["primary"]


def test_empty_chain_is_explicit() -> None:
    out, calls, _ = run({}, chain=[])
    assert out["status"] == "FAILOVER_NO_PROVIDERS"
    assert calls == []


def test_policy_rejects_invalid_bounds() -> None:
    with pytest.raises(ValueError):
        FailoverPolicy(max_attempts_per_provider=0)
    with pytest.raises(ValueError):
        FailoverPolicy(backoff_seconds=(-1.0,))


def _ready_providers() -> list[dict]:
    base = {
        "requires_live_mode": False,
        "api_key_env": None,
        "optional_dependency": None,
        "description": "test",
        "live_mode_enabled": True,
        "api_key_configured": True,
        "dependency_available": True,
        "status": "MODEL_PROVIDER_READY",
        "warnings": [],
        "modalities": ["text"],
        "context_tokens": 10000,
        "supports_streaming": False,
        "local_server": None,
    }
    return [
        {**base, "name": "anthropic", "default_model": "live", "live": True, "locality": "remote"},
        {**base, "name": "mock", "default_model": "mock", "live": False, "locality": "local"},
        {
            **base,
            "name": "openai_compatible_local",
            "default_model": "local",
            "live": False,
            "locality": "local",
            "supports_streaming": True,
        },
    ]


def test_live_route_does_not_degrade_offline_without_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.app.model_routing_policy.list_model_providers", _ready_providers)
    route = plan_model_route(task_type="cited_qa", preferred_provider="anthropic", allow_live=True)
    assert route["failover_order"] == [{"provider": "anthropic", "model": "live"}]
    assert route["invariants"]["no_implicit_offline_failover_from_live"] is True


def test_live_route_adds_ready_offline_fallbacks_only_when_opted_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.app.model_routing_policy.list_model_providers", _ready_providers)
    route = plan_model_route(
        task_type="cited_qa",
        preferred_provider="anthropic",
        allow_live=True,
        allow_offline_failover=True,
    )
    assert [row["provider"] for row in route["failover_order"]] == [
        "anthropic",
        "mock",
        "openai_compatible_local",
    ]
    assert route["invariants"]["failover_order_selected_first"] is True


def test_local_route_never_falls_back_to_mock_without_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.app.model_routing_policy.list_model_providers", _ready_providers)
    route = plan_model_route(task_type="cited_qa", preferred_provider="openai_compatible_local")
    assert route["failover_order"] == [{"provider": "openai_compatible_local", "model": "local"}]
    assert route["invariants"]["no_implicit_mock_fallback"] is True

    opted_in = plan_model_route(
        task_type="cited_qa",
        preferred_provider="openai_compatible_local",
        allow_offline_failover=True,
    )
    assert [row["provider"] for row in opted_in["failover_order"]] == ["openai_compatible_local", "mock"]
    assert opted_in["route_sha256"] != route["route_sha256"]
