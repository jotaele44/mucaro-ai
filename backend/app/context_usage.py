from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from .context_state import build_context_state, build_model_context
from .model_router import (
    LOCAL_PROVIDER,
    ModelProviderError,
    get_model_provider_spec,
    local_model_settings,
)
from .utils import utc_now

SCHEMA_VERSION = "mucaro-context-usage/1.0"
CLASSIFICATIONS = {"MEASURED", "PROVIDER_REPORTED", "COMPUTED", "ESTIMATED", "UNKNOWN"}


class ContextUsageError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "schemas" / "context_usage.schema.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ContextUsageError("ERROR_CONTEXT_USAGE_SCHEMA_UNAVAILABLE", f"{path}: {exc}") from exc


def _metric(
    value: int | float | None,
    classification: str,
    unit: str,
    source: str,
    *,
    basis: list[str] | None = None,
) -> dict[str, Any]:
    if classification not in CLASSIFICATIONS:
        raise ContextUsageError("ERROR_CONTEXT_USAGE_CLASSIFICATION", classification)
    if value is not None and (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value < 0
    ):
        raise ContextUsageError("ERROR_CONTEXT_USAGE_VALUE", f"{source}:{value}")
    if classification == "UNKNOWN" and value is not None:
        raise ContextUsageError("ERROR_CONTEXT_USAGE_UNKNOWN_VALUE", source)
    if classification != "UNKNOWN" and value is None:
        raise ContextUsageError("ERROR_CONTEXT_USAGE_MISSING_VALUE", source)
    return {
        "value": value,
        "classification": classification,
        "unit": unit,
        "source": source,
        "basis": list(basis or []),
    }


def _unknown(unit: str, source: str) -> dict[str, Any]:
    return _metric(None, "UNKNOWN", unit, source)


def _computed(value: int | float, unit: str, source: str, *basis: str) -> dict[str, Any]:
    return _metric(value, "COMPUTED", unit, source, basis=list(basis))


def _usage_metric(raw: Any, name: str) -> dict[str, Any]:
    if raw is None:
        return _unknown("tokens", f"model_usage.{name}")
    if not isinstance(raw, dict):
        raise ContextUsageError("ERROR_CONTEXT_USAGE_MODEL_USAGE", f"{name}:not_object")
    value = raw.get("value")
    classification = raw.get("classification")
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        or classification not in {"PROVIDER_REPORTED", "COMPUTED", "MEASURED"}
    ):
        raise ContextUsageError("ERROR_CONTEXT_USAGE_MODEL_USAGE", f"{name}:{raw}")
    return _metric(
        value,
        str(classification),
        "tokens",
        str(raw.get("source") or f"model_usage.{name}"),
        basis=[str(item) for item in raw.get("basis", []) if isinstance(item, str)],
    )


def _context_limit(
    model_provider: str | None,
    model_name: str | None,
) -> tuple[dict[str, Any], str | None, bool | None, list[str]]:
    if not model_provider:
        return _unknown("tokens", "model_provider_unknown"), None, None, ["WARN_CONTEXT_LIMIT_UNKNOWN"]
    try:
        spec = get_model_provider_spec(model_provider)
    except ModelProviderError as exc:
        raise ContextUsageError(exc.status, exc.detail) from exc
    if spec.name == LOCAL_PROVIDER:
        local_cfg = local_model_settings()
        configured_model = str(local_cfg["model"])
        if model_name and model_name != configured_model:
            return (
                _unknown("tokens", "local_model_context_override_unbound"),
                spec.locality,
                spec.supports_streaming,
                ["WARN_CONTEXT_LIMIT_MODEL_OVERRIDE_UNBOUND"],
            )
        value = int(local_cfg["context_tokens"])
        source = "local_model_settings.context_tokens"
    else:
        if model_name and model_name != spec.default_model:
            return (
                _unknown("tokens", f"model_provider_spec:{spec.name}.override_unbound"),
                spec.locality,
                spec.supports_streaming,
                ["WARN_CONTEXT_LIMIT_MODEL_OVERRIDE_UNBOUND"],
            )
        value = int(spec.context_tokens)
        source = f"model_provider_spec:{spec.name}.context_tokens"
    return (
        _computed(value, "tokens", source),
        spec.locality,
        spec.supports_streaming,
        [],
    )


def _raw_text(conversation: dict[str, Any]) -> str:
    return "\n".join(str(turn.get("content", "")) for turn in conversation.get("turns", []))


def _model_context_text(model_context: list[dict[str, Any]]) -> str:
    return "\n".join(str(turn.get("content", "")) for turn in model_context)


def _validate(payload: dict[str, Any]) -> dict[str, Any]:
    errors = sorted(
        jsonschema.Draft202012Validator(_schema()).iter_errors(payload),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        where = "/".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise ContextUsageError("ERROR_CONTEXT_USAGE_SCHEMA", f"{where}: {errors[0].message}")
    return payload


def build_context_usage_status(
    conversation: dict[str, Any],
    *,
    model_provider: str | None = None,
    model_name: str | None = None,
    run_mode: str | None = None,
    answer_trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(conversation, dict):
        raise ContextUsageError("ERROR_CONTEXT_USAGE_CONVERSATION", "conversation must be an object")

    trace = answer_trace if isinstance(answer_trace, dict) else {}
    provider = model_provider or trace.get("model_provider")
    name = model_name or trace.get("model_name")

    context_state = build_context_state(conversation)
    model_context = build_model_context(context_state)
    raw_text = _raw_text(conversation)
    model_text = _model_context_text(model_context)
    recent_text = "\n".join(
        str(turn.get("content", ""))
        for turn in context_state.get("recent_context", [])
    )
    summary_text = str(context_state.get("compacted_summary", ""))

    limit_metric, locality, supports_streaming, warnings = _context_limit(
        str(provider) if provider else None,
        str(name) if name else None,
    )

    model_usage = trace.get("model_usage")
    if model_usage is not None and not isinstance(model_usage, dict):
        raise ContextUsageError("ERROR_CONTEXT_USAGE_MODEL_USAGE", "model_usage must be an object")
    model_usage = model_usage or {}

    input_tokens = _usage_metric(model_usage.get("input_tokens"), "input_tokens")
    cache_creation = _usage_metric(
        model_usage.get("cache_creation_input_tokens"),
        "cache_creation_input_tokens",
    )
    cache_read = _usage_metric(
        model_usage.get("cache_read_input_tokens"),
        "cache_read_input_tokens",
    )
    output_tokens = _usage_metric(model_usage.get("output_tokens"), "output_tokens")

    input_parts = [
        metric
        for metric in (input_tokens, cache_creation, cache_read)
        if metric["value"] is not None
    ]
    if input_parts and input_tokens["value"] is not None:
        effective_input_value = sum(int(metric["value"]) for metric in input_parts)
        effective_input = _computed(
            effective_input_value,
            "tokens",
            "sum_provider_input_categories",
            *[
                metric["source"]
                for metric in input_parts
            ],
        )
    else:
        effective_input = _unknown("tokens", "provider_input_tokens_unavailable")

    if effective_input["value"] is not None and output_tokens["value"] is not None:
        consumed = _computed(
            int(effective_input["value"]) + int(output_tokens["value"]),
            "tokens",
            "effective_input_plus_output",
            effective_input["source"],
            output_tokens["source"],
        )
    else:
        consumed = _unknown("tokens", "provider_token_consumption_unavailable")

    if limit_metric["value"] is not None and consumed["value"] is not None:
        remaining_value = max(0, int(limit_metric["value"]) - int(consumed["value"]))
        remaining = _computed(
            remaining_value,
            "tokens",
            "context_limit_minus_consumed",
            limit_metric["source"],
            consumed["source"],
        )
        utilization = _computed(
            min(1.0, float(consumed["value"]) / float(limit_metric["value"]))
            if float(limit_metric["value"]) > 0
            else 0.0,
            "ratio",
            "consumed_divided_by_context_limit",
            consumed["source"],
            limit_metric["source"],
        )
    else:
        remaining = _unknown("tokens", "remaining_context_unavailable")
        utilization = _unknown("ratio", "context_utilization_unavailable")

    total_tokens = model_usage.get("total_tokens")
    if total_tokens is not None:
        total_metric = _usage_metric(total_tokens, "total_tokens")
    elif consumed["value"] is not None:
        total_metric = _computed(
            int(consumed["value"]),
            "tokens",
            "effective_input_plus_output",
            consumed["source"],
        )
    else:
        total_metric = _unknown("tokens", "provider_total_tokens_unavailable")

    if (
        total_metric["value"] is not None
        and consumed["value"] is not None
        and int(total_metric["value"]) != int(consumed["value"])
    ):
        warnings.append("WARN_PROVIDER_TOTAL_TOKEN_MISMATCH")

    answer = str(trace.get("answer", "")) if trace else ""
    cost = model_usage.get("cost_microunits")
    if cost is None:
        cost_metric = _unknown("microunits", "provider_cost_unavailable")
    else:
        if not isinstance(cost, dict):
            raise ContextUsageError("ERROR_CONTEXT_USAGE_MODEL_USAGE", "cost_microunits:not_object")
        cost_metric = _metric(
            cost.get("value"),
            str(cost.get("classification")),
            "microunits",
            str(cost.get("source") or "model_usage.cost_microunits"),
            basis=[str(item) for item in cost.get("basis", []) if isinstance(item, str)],
        )

    if input_tokens["value"] is None:
        warnings.append("WARN_INPUT_TOKENS_UNKNOWN")
    if output_tokens["value"] is None:
        warnings.append("WARN_OUTPUT_TOKENS_UNKNOWN")
    if cost_metric["value"] is None:
        warnings.append("WARN_MODEL_COST_UNKNOWN")

    payload = {
        "schema_version": SCHEMA_VERSION,
        "created_at": utc_now(),
        "conversation_id": conversation.get("conversation_id"),
        "model": {
            "provider": str(provider) if provider else None,
            "name": str(name) if name else None,
            "run_mode": str(run_mode) if run_mode else None,
            "locality": locality,
            "supports_streaming": supports_streaming,
        },
        "context_state": {
            "context_state_id": context_state["context_state_id"],
            "raw_turn_count": int(context_state["raw_turn_count"]),
            "historical_turn_count": int(context_state["historical_turn_count"]),
            "recent_turn_count": int(context_state["recent_turn_count"]),
            "represented_historical_turns": int(context_state["coverage"]["represented_historical_turns"]),
            "total_historical_turns": int(context_state["coverage"]["total_historical_turns"]),
            "coverage_ratio": float(context_state["coverage"]["ratio"]),
            "raw_history_mutated": bool(context_state["policy"]["raw_history_mutated"]),
        },
        "metrics": {
            "raw_context_chars": _computed(len(raw_text), "chars", "canonical_conversation_turns"),
            "raw_context_bytes": _computed(len(raw_text.encode("utf-8")), "bytes", "canonical_conversation_turns"),
            "model_context_chars": _computed(len(model_text), "chars", "build_model_context"),
            "model_context_bytes": _computed(len(model_text.encode("utf-8")), "bytes", "build_model_context"),
            "compacted_summary_chars": _computed(len(summary_text), "chars", "context_state.compacted_summary"),
            "recent_context_chars": _computed(len(recent_text), "chars", "context_state.recent_context"),
            "context_limit_tokens": limit_metric,
            "input_tokens": input_tokens,
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
            "effective_input_tokens": effective_input,
            "output_tokens": output_tokens,
            "total_tokens": total_metric,
            "context_consumed_tokens": consumed,
            "remaining_context_tokens": remaining,
            "context_utilization_ratio": utilization,
            "answer_chars": _computed(len(answer), "chars", "answer_trace.answer"),
            "answer_bytes": _computed(len(answer.encode("utf-8")), "bytes", "answer_trace.answer"),
            "cost_microunits": cost_metric,
        },
        "warnings": sorted(set(warnings)),
    }
    return _validate(payload)
