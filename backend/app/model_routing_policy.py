from __future__ import annotations

import hashlib
import json
from typing import Any

from .model_router import ModelProviderError, get_model_provider_spec, list_model_providers, normalize_model_provider
from .contracts import validate_contract

MODEL_ROUTING_SCHEMA_VERSION = "mucaro-model-routing/1.0"
TASK_TYPES = {"cited_qa", "coding", "analysis", "summarization", "extraction", "conversation"}


class ModelRoutingError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _stable_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def plan_model_route(
    *,
    task_type: str,
    preferred_provider: str | None = None,
    preferred_model: str | None = None,
    allowed_providers: list[str] | None = None,
    require_live: bool = False,
    allow_live: bool = False,
    require_offline: bool = False,
    require_citations: bool = True,
) -> dict[str, Any]:
    task = task_type.strip().lower() if isinstance(task_type, str) else ""
    if task not in TASK_TYPES:
        raise ModelRoutingError("ERROR_MODEL_ROUTING_TASK", f"Unsupported task_type: {task_type}")
    if require_live and require_offline:
        raise ModelRoutingError("ERROR_MODEL_ROUTING_CONSTRAINT_CONFLICT", "require_live and require_offline cannot both be true")
    if require_live and not allow_live:
        raise ModelRoutingError("ERROR_MODEL_ROUTING_LIVE_NOT_ALLOWED", "Live routing requires explicit allow_live=true")

    provider_rows = {row["name"]: row for row in list_model_providers()}
    known = set(provider_rows)
    if allowed_providers is None:
        allowed = sorted(known)
    else:
        if not isinstance(allowed_providers, list) or not allowed_providers:
            raise ModelRoutingError("ERROR_MODEL_ROUTING_ALLOWLIST", "allowed_providers must be a non-empty list")
        allowed = []
        for raw in allowed_providers:
            provider = normalize_model_provider(raw)
            if provider not in known:
                raise ModelRoutingError("ERROR_UNSUPPORTED_MODEL_PROVIDER", provider)
            if provider not in allowed:
                allowed.append(provider)

    preferred = normalize_model_provider(preferred_provider) if preferred_provider is not None else None
    if preferred is not None and preferred not in known:
        raise ModelRoutingError("ERROR_UNSUPPORTED_MODEL_PROVIDER", preferred)
    if preferred is not None and preferred not in allowed:
        raise ModelRoutingError("ERROR_MODEL_ROUTING_PROVIDER_NOT_ALLOWED", preferred)

    candidates: list[dict[str, Any]] = []
    for provider in allowed:
        status = provider_rows[provider]
        live = bool(status["live"])
        reasons: list[str] = []
        eligible = True
        if live and not allow_live:
            eligible = False
            reasons.append("LIVE_PROVIDER_NOT_EXPLICITLY_ALLOWED")
        if require_offline and live:
            eligible = False
            reasons.append("OFFLINE_REQUIRED")
        if require_live and not live:
            eligible = False
            reasons.append("LIVE_REQUIRED")
        if status["status"] != "MODEL_PROVIDER_READY":
            eligible = False
            reasons.append(f"PROVIDER_STATUS:{status['status']}")
        candidates.append(
            {
                "provider": provider,
                "default_model": status["default_model"],
                "live": live,
                "provider_status": status["status"],
                "warnings": list(status.get("warnings") or []),
                "eligible": eligible,
                "reasons": reasons,
            }
        )

    eligible = [row for row in candidates if row["eligible"]]
    selected: dict[str, Any] | None = None
    selection_reason = None
    if preferred is not None:
        selected = next((row for row in eligible if row["provider"] == preferred), None)
        if selected is None:
            selection_reason = "PREFERRED_PROVIDER_NOT_ELIGIBLE"
        else:
            selection_reason = "EXPLICIT_PREFERRED_PROVIDER"
    else:
        # Never escalate to a live provider merely because credentials happen to exist.
        offline = [row for row in eligible if not row["live"]]
        if require_live:
            selected = eligible[0] if eligible else None
            selection_reason = "EXPLICIT_LIVE_REQUIREMENT" if selected else "NO_ELIGIBLE_LIVE_PROVIDER"
        elif offline:
            selected = offline[0]
            selection_reason = "SAFE_OFFLINE_DEFAULT"
        elif allow_live and eligible:
            selected = eligible[0]
            selection_reason = "EXPLICIT_LIVE_FALLBACK_ALLOWED"
        else:
            selection_reason = "NO_ELIGIBLE_PROVIDER"

    selected_provider = selected["provider"] if selected else None
    selected_model = preferred_model.strip() if isinstance(preferred_model, str) and preferred_model.strip() else (
        selected["default_model"] if selected else None
    )
    status = "MODEL_ROUTE_READY" if selected else "MODEL_ROUTE_BLOCKED"
    payload: dict[str, Any] = {
        "schema_version": MODEL_ROUTING_SCHEMA_VERSION,
        "status": status,
        "task_type": task,
        "constraints": {
            "preferred_provider": preferred,
            "preferred_model": preferred_model,
            "allowed_providers": allowed,
            "require_live": require_live,
            "allow_live": allow_live,
            "require_offline": require_offline,
            "require_citations": require_citations,
        },
        "selected_provider": selected_provider,
        "selected_model": selected_model,
        "selection_reason": selection_reason,
        "candidates": candidates,
        "invariants": {
            "selected_provider_is_allowed": selected_provider is None or selected_provider in allowed,
            "no_implicit_live_escalation": selected is None or not selected["live"] or allow_live,
            "offline_constraint_honored": not require_offline or selected is None or not selected["live"],
            "live_constraint_honored": not require_live or selected is None or selected["live"],
            "selected_provider_ready": selected is None or selected["provider_status"] == "MODEL_PROVIDER_READY",
        },
        "limitations": [
            "Routing is policy selection over registered providers; it does not estimate answer quality or cost from unobserved future outputs.",
            "Live providers are never selected solely because credentials are present; live use requires explicit allow_live=true.",
            "Citation requirements remain enforced by the answer-trace layer and are not proven by provider selection alone.",
        ],
        "falsification_criteria": [
            "never select a provider outside the explicit allowlist",
            "never select a live provider without explicit live permission",
            "never report READY for a provider whose readiness status is not MODEL_PROVIDER_READY",
            "constraint conflicts fail closed",
        ],
    }
    payload["route_sha256"] = _stable_hash(payload)
    return validate_contract("model_route_decision", payload)
