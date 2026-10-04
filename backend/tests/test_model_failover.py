from __future__ import annotations

import pytest

from backend.app.contracts import ContractViolation, list_contracts, validate_contract
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
    assert [attempt["status"] for attempt in out["attempts"]] == ["OK"]


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


def test_unavailable_skips_retries_and_fails_over_immediately() -> None:
    out, calls, sleeps = run({"primary": [DOWN], "secondary": [{"answer": "b"}]})
    assert calls == ["primary", "secondary"]
    assert sleeps == []
    assert out["provider"] == "secondary"


def test_local_unavailable_can_fail_over_and_counts_as_interruption_if_exhausted() -> None:
    out, calls, _ = run({"primary": [LOCAL_DOWN], "secondary": [DOWN]})
    assert calls == ["primary", "secondary"]
    assert out["status"] == "FAILOVER_EXHAUSTED"
    assert out["interruption"] is True


def test_malformed_response_can_fail_over_but_does_not_make_exhaustion_an_interruption() -> None:
    out, calls, _ = run({"primary": [DOWN], "secondary": [MALFORMED]})
    assert calls == ["primary", "secondary"]
    assert out["status"] == "FAILOVER_EXHAUSTED"
    assert out["interruption"] is False


def test_hard_failure_stops_the_chain_and_is_not_masked() -> None:
    out, calls, _ = run({"primary": [HARD], "secondary": [{"answer": "b"}]})
    assert out["status"] == "FAILOVER_HARD_FAILURE"
    assert out["error_status"] == HARD
    assert calls == ["primary"]


def test_empty_chain_is_reported_not_raised() -> None:
    out, calls, _ = run({}, chain=[])
    assert out["status"] == "FAILOVER_NO_PROVIDERS"
    assert calls == []


def test_policy_rejects_invalid_attempts_and_negative_backoff() -> None:
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


def test_live_route_never_adds_offline_fallback_without_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.app.model_routing_policy.list_model_providers", _ready_providers)
    route = plan_model_route(task_type="cited_qa", preferred_provider="anthropic", allow_live=True)
    assert route["failover_order"] == [{"provider": "anthropic", "model": "live"}]
    assert route["invariants"]["no_implicit_offline_failover_from_live"] is True


def test_live_route_adds_only_ready_allowed_offline_fallbacks_when_opted_in(monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert route["route_sha256"]
    assert route["invariants"]["failover_order_selected_first"] is True


def test_local_route_never_falls_back_to_mock_without_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.app.model_routing_policy.list_model_providers", _ready_providers)
    route = plan_model_route(
        task_type="cited_qa",
        preferred_provider="openai_compatible_local",
    )
    assert route["failover_order"] == [
        {"provider": "openai_compatible_local", "model": "local"},
    ]
    assert route["invariants"]["no_implicit_mock_fallback"] is True

    opted_in = plan_model_route(
        task_type="cited_qa",
        preferred_provider="openai_compatible_local",
        allow_offline_failover=True,
    )
    assert [row["provider"] for row in opted_in["failover_order"]] == [
        "openai_compatible_local",
        "mock",
    ]
    assert opted_in["route_sha256"] != route["route_sha256"]


@pytest.mark.parametrize(
    "script",
    [
        {"primary": [{"a": 1}], "secondary": []},
        {"primary": [RATE, RATE], "secondary": [DOWN]},
        {"primary": [HARD], "secondary": []},
    ],
)
def test_failover_receipts_validate_as_complete_canonical_contracts(script) -> None:
    out, _, _ = run(script)
    assert validate_contract("model_failover", out) is out


def test_model_failover_is_registered_in_model_routing_contract_family() -> None:
    contracts = list_contracts()
    routing = next(row for row in contracts["families"] if row["family"] == "MODEL_ROUTING")
    kinds = {row["kind"] for row in routing["record_kinds"]}
    assert "model_failover" in kinds
    assert next(row for row in routing["record_kinds"] if row["kind"] == "model_failover")["schema_present"] is True


def test_failover_contract_rejects_success_without_provider_identity() -> None:
    with pytest.raises(ContractViolation):
        validate_contract(
            "model_failover",
            {
                "schema_version": "mucaro-model-failover/1.0",
                "status": "FAILOVER_OK",
                "attempts": [],
                "failed_over": False,
                "result": {},
            },
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "schema_version": "mucaro-model-failover/1.0",
            "status": "FAILOVER_OK",
            "provider": "primary",
            "model": "m1",
            "result": {},
            "failed_over": False,
            "attempts": [
                {
                    "attempt": 1,
                    "provider": "primary",
                    "model": "m1",
                    "status": "OK",
                    "detail": "",
                    "started_at": "2026-09-30T00:00:00Z",
                    "duration_ms": 1,
                }
            ],
            "interruption": False,
        },
        {
            "schema_version": "mucaro-model-failover/1.0",
            "status": "FAILOVER_EXHAUSTED",
            "error_status": DOWN,
            "detail": "down",
            "interruption": True,
            "attempts": [
                {
                    "attempt": 1,
                    "provider": "primary",
                    "model": "m1",
                    "status": DOWN,
                    "detail": "down",
                    "started_at": "2026-09-30T00:00:00Z",
                    "duration_ms": 1,
                }
            ],
            "provider": "primary",
        },
        {
            "schema_version": "mucaro-model-failover/1.0",
            "status": "FAILOVER_NO_PROVIDERS",
            "attempts": [],
            "interruption": False,
            "error_status": "ERROR_SHOULD_NOT_EXIST",
        },
    ],
)
def test_failover_contract_rejects_status_incompatible_fields(payload: dict) -> None:
    with pytest.raises(ContractViolation):
        validate_contract("model_failover", payload)


def test_failover_contract_requires_an_attempt_for_executed_terminal_states() -> None:
    with pytest.raises(ContractViolation):
        validate_contract(
            "model_failover",
            {
                "schema_version": "mucaro-model-failover/1.0",
                "status": "FAILOVER_HARD_FAILURE",
                "error_status": HARD,
                "detail": "hard",
                "interruption": False,
                "attempts": [],
            },
        )
