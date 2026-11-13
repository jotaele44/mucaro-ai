from __future__ import annotations

from typing import Any

TERMINAL_CONTEXT_STATUS_SCHEMA_VERSION = "mucaro-terminal-context-status/1.0"


def _metric_value(metrics: dict[str, Any], name: str) -> int | None:
    metric = metrics.get(name)
    if not isinstance(metric, dict):
        return None
    value = metric.get("value")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def build_terminal_context_status(run: dict[str, Any]) -> dict[str, Any]:
    """Project canonical run context usage into a non-estimating terminal status."""
    usage = run.get("context_usage")
    metrics = usage.get("metrics") if isinstance(usage, dict) else None
    metrics = metrics if isinstance(metrics, dict) else {}

    consumed = _metric_value(metrics, "context_consumed_tokens")
    limit = _metric_value(metrics, "context_limit_tokens")
    consumed_text = "?" if consumed is None else str(consumed)
    limit_text = "?" if limit is None else str(limit)

    model = usage.get("model") if isinstance(usage, dict) else None
    model = model if isinstance(model, dict) else {}
    warnings = usage.get("warnings") if isinstance(usage, dict) else None

    return {
        "schema_version": TERMINAL_CONTEXT_STATUS_SCHEMA_VERSION,
        "status": "CONTEXT_STATUS_OK" if consumed is not None and limit is not None else "CONTEXT_STATUS_PARTIAL",
        "run_id": run.get("run_id"),
        "run_state": run.get("state"),
        "model": {
            "provider": model.get("provider"),
            "name": model.get("name"),
        },
        "context_consumed_tokens": consumed,
        "context_limit_tokens": limit,
        "display": f"ctx {consumed_text}/{limit_text}",
        "warnings": list(warnings) if isinstance(warnings, list) else [],
        "invariants": {
            "source": "work_runs.context_usage",
            "estimated_missing_values": False,
            "unknown_values_render_as_question_mark": True,
        },
    }
