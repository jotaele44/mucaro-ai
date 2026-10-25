from __future__ import annotations

import pytest

from backend.app.context_usage import ContextUsageError, build_context_usage_status
from backend.app.model_router import _build_model_usage


def conversation() -> dict:
    return {
        "conversation_id": "conv_context",
        "turns": [
            {
                "turn_id": "turn_1",
                "role": "user",
                "content": "Alpha " * 100,
                "status": "USER_MESSAGE",
            },
            {
                "turn_id": "turn_2",
                "role": "assistant",
                "content": "Beta " * 120,
                "status": "DONE",
            },
        ],
    }


def test_context_usage_without_provider_tokens_stays_explicitly_unknown() -> None:
    status = build_context_usage_status(
        conversation(),
        model_provider="mock",
        model_name="mock-cited-synthesizer-v1",
        run_mode="ask",
        answer_trace={
            "model_provider": "mock",
            "model_name": "mock-cited-synthesizer-v1",
            "answer": "answer",
            "model_usage": None,
        },
    )

    metrics = status["metrics"]
    assert metrics["raw_context_chars"]["classification"] == "COMPUTED"
    assert metrics["raw_context_bytes"]["classification"] == "COMPUTED"
    assert metrics["model_context_chars"]["classification"] == "COMPUTED"
    assert metrics["context_limit_tokens"] == {
        "value": 1_000_000,
        "classification": "COMPUTED",
        "unit": "tokens",
        "source": "model_provider_spec:mock.context_tokens",
        "basis": [],
    }
    for key in (
        "input_tokens",
        "output_tokens",
        "effective_input_tokens",
        "context_consumed_tokens",
        "total_tokens",
        "remaining_context_tokens",
        "context_utilization_ratio",
        "cost_microunits",
    ):
        assert metrics[key]["value"] is None
        assert metrics[key]["classification"] == "UNKNOWN"
    assert "WARN_INPUT_TOKENS_UNKNOWN" in status["warnings"]
    assert "WARN_OUTPUT_TOKENS_UNKNOWN" in status["warnings"]
    assert "WARN_MODEL_COST_UNKNOWN" in status["warnings"]


def test_provider_reported_usage_drives_only_supported_computations() -> None:
    model_usage = {
        "input_tokens": {
            "value": 100,
            "classification": "PROVIDER_REPORTED",
            "source": "anthropic.response.usage.input_tokens",
            "basis": [],
        },
        "cache_creation_input_tokens": {
            "value": 20,
            "classification": "PROVIDER_REPORTED",
            "source": "anthropic.response.usage.cache_creation_input_tokens",
            "basis": [],
        },
        "cache_read_input_tokens": {
            "value": 5,
            "classification": "PROVIDER_REPORTED",
            "source": "anthropic.response.usage.cache_read_input_tokens",
            "basis": [],
        },
        "output_tokens": {
            "value": 25,
            "classification": "PROVIDER_REPORTED",
            "source": "anthropic.response.usage.output_tokens",
            "basis": [],
        },
        "total_tokens": {
            "value": 150,
            "classification": "PROVIDER_REPORTED",
            "source": "anthropic.response.usage.total_tokens",
            "basis": [],
        },
    }
    status = build_context_usage_status(
        conversation(),
        model_provider="anthropic",
        model_name="claude-sonnet-5",
        answer_trace={
            "model_provider": "anthropic",
            "model_name": "claude-sonnet-5",
            "answer": "answer",
            "model_usage": model_usage,
        },
    )
    metrics = status["metrics"]

    assert metrics["input_tokens"]["value"] == 100
    assert metrics["input_tokens"]["classification"] == "PROVIDER_REPORTED"
    assert metrics["effective_input_tokens"]["value"] == 125
    assert metrics["effective_input_tokens"]["classification"] == "COMPUTED"
    assert metrics["context_consumed_tokens"]["value"] == 150
    assert metrics["total_tokens"]["value"] == 150
    assert metrics["total_tokens"]["classification"] == "PROVIDER_REPORTED"
    assert metrics["remaining_context_tokens"]["value"] == 199_850
    assert metrics["context_utilization_ratio"]["value"] == pytest.approx(150 / 200_000)
    assert "WARN_PROVIDER_TOTAL_TOKEN_MISMATCH" not in status["warnings"]


def test_provider_total_token_conflict_is_preserved_as_warning() -> None:
    status = build_context_usage_status(
        conversation(),
        model_provider="anthropic",
        model_name="claude-sonnet-5",
        answer_trace={
            "model_provider": "anthropic",
            "model_name": "claude-sonnet-5",
            "answer": "answer",
            "model_usage": {
                "input_tokens": {
                    "value": 100,
                    "classification": "PROVIDER_REPORTED",
                    "source": "provider.input",
                    "basis": [],
                },
                "output_tokens": {
                    "value": 20,
                    "classification": "PROVIDER_REPORTED",
                    "source": "provider.output",
                    "basis": [],
                },
                "total_tokens": {
                    "value": 999,
                    "classification": "PROVIDER_REPORTED",
                    "source": "provider.total",
                    "basis": [],
                },
            },
        },
    )

    assert status["metrics"]["context_consumed_tokens"]["value"] == 120
    assert status["metrics"]["total_tokens"]["value"] == 999
    assert "WARN_PROVIDER_TOTAL_TOKEN_MISMATCH" in status["warnings"]


def test_model_override_does_not_inherit_unproven_provider_context_limit() -> None:
    status = build_context_usage_status(
        conversation(),
        model_provider="anthropic",
        model_name="different-model",
        answer_trace={
            "model_provider": "anthropic",
            "model_name": "different-model",
            "answer": "answer",
        },
    )

    assert status["metrics"]["context_limit_tokens"]["classification"] == "UNKNOWN"
    assert status["metrics"]["context_limit_tokens"]["value"] is None
    assert status["metrics"]["remaining_context_tokens"]["classification"] == "UNKNOWN"
    assert "WARN_CONTEXT_LIMIT_MODEL_OVERRIDE_UNBOUND" in status["warnings"]


def test_invalid_provider_usage_fails_closed() -> None:
    with pytest.raises(ContextUsageError) as exc:
        build_context_usage_status(
            conversation(),
            model_provider="mock",
            answer_trace={
                "model_provider": "mock",
                "model_name": "mock-cited-synthesizer-v1",
                "answer": "answer",
                "model_usage": {
                    "input_tokens": {
                        "value": -1,
                        "classification": "PROVIDER_REPORTED",
                        "source": "provider.input",
                        "basis": [],
                    }
                },
            },
        )
    assert exc.value.status == "ERROR_CONTEXT_USAGE_MODEL_USAGE"


def test_model_router_usage_helper_preserves_provider_reported_classification() -> None:
    usage = _build_model_usage(
        input_tokens=10,
        output_tokens=3,
        total_tokens=13,
        source_prefix="fixture.response.usage",
    )
    assert usage == {
        "input_tokens": {
            "value": 10,
            "classification": "PROVIDER_REPORTED",
            "source": "fixture.response.usage.input_tokens",
            "basis": [],
        },
        "output_tokens": {
            "value": 3,
            "classification": "PROVIDER_REPORTED",
            "source": "fixture.response.usage.output_tokens",
            "basis": [],
        },
        "total_tokens": {
            "value": 13,
            "classification": "PROVIDER_REPORTED",
            "source": "fixture.response.usage.total_tokens",
            "basis": [],
        },
    }
