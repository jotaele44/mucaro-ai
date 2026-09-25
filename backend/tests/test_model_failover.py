from __future__ import annotations

import pytest

from backend.app.model_failover import FailoverPolicy, run_with_failover
from backend.app.model_router import ModelProviderError
from backend.app.model_routing_policy import plan_model_route

RATE = "ERROR_MODEL_PROVIDER_RATE_LIMIT"
TIMEOUT = "ERROR_MODEL_PROVIDER_TIMEOUT"
DOWN = "ERROR_MODEL_PROVIDER_UNAVAILABLE"
MALFORMED = "ERROR_MODEL_PROVIDER_MALFORMED_RESPONSE"
HARD = "ERROR_MODEL_PROVIDER_CALL_FAILED"
CHAIN = [{"provider": "primary", "model": "m1"}, {"provider": "secondary", "model": "m2"}]


def scripted(script: dict[str, list]):
    """script[provider] = list of statuses or dict results, consumed in order."""
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
    assert out["status"] == "FAILOVER_OK" and out["provider"] == "primary" and out["failed_over"] is False
    assert calls == ["primary"] and sleeps == []
    assert [a["status"] for a in out["attempts"]] == ["OK"]


def test_rate_limit_retries_with_backoff_then_succeeds_on_same_provider() -> None:
    out, calls, sleeps = run({"primary": [RATE, {"answer": "a"}], "secondary": []})
    assert out["provider"] == "primary" and calls == ["primary", "primary"]
    assert sleeps == [0.5]
    assert [a["status"] for a in out["attempts"]] == [RATE, "OK"]


def test_retries_are_bounded_then_failover_to_next_provider() -> None:
    out, calls, sleeps = run({"primary": [TIMEOUT, TIMEOUT], "secondary": [{"answer": "b"}]})
    assert out["provider"] == "secondary" and out["failed_over"] is True
    assert calls == ["primary", "primary", "secondary"] and sleeps == [0.5]


def test_unavailable_skips_retries_and_fails_over_immediately() -> None:
    out, calls, sleeps = run({"primary": [DOWN], "secondary": [{"answer": "b"}]})
    assert calls == ["primary", "secondary"] and sleeps == []
    assert out["provider"] == "secondary"


def test_malformed_response_fails_over_without_retry() -> None:
    out, calls, _ = run({"primary": [MALFORMED], "secondary": [{"answer": "b"}]})
    assert calls == ["primary", "secondary"] and out["status"] == "FAILOVER_OK"


def test_hard_failure_stops_the_chain_and_is_not_masked_by_failover() -> None:
    out, calls, _ = run({"primary": [HARD], "secondary": [{"answer": "b"}]})
    assert out["status"] == "FAILOVER_HARD_FAILURE" and out["error_status"] == HARD
    assert calls == ["primary"]


def test_pure_outage_is_reported_as_interruption() -> None:
    out, calls, _ = run({"primary": [RATE, RATE], "secondary": [DOWN]})
    assert out["status"] == "FAILOVER_EXHAUSTED" and out["interruption"] is True
    assert len(out["attempts"]) == 3 and calls == ["primary", "primary", "secondary"]


def test_malformed_among_failures_is_not_an_interruption() -> None:
    out, _, _ = run({"primary": [DOWN], "secondary": [MALFORMED]})
    assert out["status"] == "FAILOVER_EXHAUSTED" and out["interruption"] is False


def test_empty_chain_is_reported_not_raised() -> None:
    out, calls, _ = run({}, chain=[])
    assert out["status"] == "FAILOVER_NO_PROVIDERS" and calls == []


def test_backoff_clamps_to_last_delay() -> None:
    out, _, sleeps = run({"primary": [RATE, RATE, RATE, {"a": 1}]}, chain=CHAIN[:1], policy=FailoverPolicy(max_attempts_per_provider=4, backoff_seconds=(1.0, 3.0)))
    assert out["status"] == "FAILOVER_OK" and sleeps == [1.0, 3.0, 3.0]


def test_policy_rejects_zero_attempts() -> None:
    with pytest.raises(ValueError):
        FailoverPolicy(max_attempts_per_provider=0)


def test_attempts_are_recorded_in_order_with_timing_fields() -> None:
    out, _, _ = run({"primary": [DOWN], "secondary": [{"answer": "b"}]})
    assert [a["attempt"] for a in out["attempts"]] == [1, 2]
    assert all({"provider", "model", "status", "started_at", "duration_ms"} <= set(a) for a in out["attempts"])


def test_route_failover_order_keeps_live_from_degrading_to_offline_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUCARO_AI_LIVE_MODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr("backend.app.model_router.importlib.util.find_spec", lambda name: object())
    live = plan_model_route(task_type="cited_qa", preferred_provider="anthropic", allow_live=True)
    assert [row["provider"] for row in live["failover_order"]] == ["anthropic"]
    assert live["invariants"]["no_implicit_offline_failover_from_live"] is True
    opted_in = plan_model_route(task_type="cited_qa", preferred_provider="anthropic", allow_live=True, allow_offline_failover=True)
    assert [row["provider"] for row in opted_in["failover_order"]] == ["anthropic", "mock"]
    assert opted_in["route_sha256"] != live["route_sha256"]


def test_offline_default_route_has_single_entry_failover_order() -> None:
    route = plan_model_route(task_type="cited_qa")
    assert [row["provider"] for row in route["failover_order"]] == ["mock"]


@pytest.mark.parametrize(
    "script",
    [
        {"primary": [{"a": 1}], "secondary": []},
        {"primary": [RATE, RATE], "secondary": [DOWN]},
        {"primary": [HARD], "secondary": []},
    ],
)
def test_failover_receipts_validate_against_published_schema(script) -> None:
    import json
    from pathlib import Path

    import jsonschema

    schema = json.loads((Path(__file__).resolve().parents[2] / "schemas" / "model_failover.schema.json").read_text())
    out, _, _ = run(script)
    jsonschema.validate({k: v for k, v in out.items() if k != "result"}, schema)
