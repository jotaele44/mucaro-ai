from __future__ import annotations

import hashlib
import json
from typing import Any

from .contracts import validate_contract
from .model_router import list_model_providers, normalize_model_provider

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
    required_modalities: list[str] | None = None,
    min_context_tokens: int | None = None,
    system_offline: bool = False,
    allow_offline_failover: bool = False,
) -> dict[str, Any]:
    """Select a provider under explicit constraints (TGT-010, MATRIX-012).

    require_offline / allow_live=False keep content on this machine; system_offline
    reports that the network is unavailable, which makes remote providers ineligible
    rather than hanging on a connection attempt.
    """
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

    modalities_needed = sorted({str(item).lower() for item in (required_modalities or ["text"])})
    candidates: list[dict[str, Any]] = []
    for provider in allowed:
        status = provider_rows[provider]
        live = bool(status["live"])
        reasons: list[str] = []
        eligible = True
        missing_modalities = [item for item in modalities_needed if item not in status.get("modalities", ["text"])]
        if missing_modalities:
            eligible = False
            reasons.append(f"MODALITY_UNSUPPORTED:{','.join(missing_modalities)}")
        if min_context_tokens is not None and int(status.get("context_tokens") or 0) < min_context_tokens:
            eligible = False
            reasons.append(f"CONTEXT_TOO_SMALL:{status.get('context_tokens')}<{min_context_tokens}")
        if system_offline and status.get("locality") == "remote":
            eligible = False
            reasons.append("SYSTEM_OFFLINE")
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
                "locality": status.get("locality"),
                "provider_status": status["status"],
                "warnings": list(status.get("warnings") or []),
                "eligible": eligible,
                "reasons": reasons,
            }
        )

    eligible_rows = [row for row in candidates if row["eligible"]]
    selected: dict[str, Any] | None = None
    selection_reason = None
    if preferred is not None:
        selected = next((row for row in eligible_rows if row["provider"] == preferred), None)
        if selected is None:
            selection_reason = "PREFERRED_PROVIDER_NOT_ELIGIBLE"
        else:
            selection_reason = "EXPLICIT_PREFERRED_PROVIDER"
    else:
        # Never escalate to a live provider merely because credentials happen to exist.
        offline = [row for row in eligible_rows if not row["live"]]
        if require_live:
            selected = eligible_rows[0] if eligible_rows else None
            selection_reason = "EXPLICIT_LIVE_REQUIREMENT" if selected else "NO_ELIGIBLE_LIVE_PROVIDER"
        elif offline:
            selected = offline[0]
            selection_reason = "SAFE_OFFLINE_DEFAULT"
        elif allow_live and eligible_rows:
            selected = eligible_rows[0]
            selection_reason = "EXPLICIT_LIVE_FALLBACK_ALLOWED"
        else:
            selection_reason = "NO_ELIGIBLE_PROVIDER"

    selected_provider = selected["provider"] if selected else None
    selected_model = preferred_model.strip() if isinstance(preferred_model, str) and preferred_model.strip() else (
        selected["default_model"] if selected else None
    )
    route_status = "MODEL_ROUTE_READY" if selected else "MODEL_ROUTE_BLOCKED"
    failover_order: list[dict[str, Any]] = []
    if selected is not None:
        failover_order.append({"provider": selected["provider"], "model": selected_model})
        for row in eligible_rows:
            if row["provider"] == selected["provider"]:
                continue
            if row["provider"] == "mock" and selected["provider"] != "mock" and not allow_offline_failover:
                continue
            if selected["live"] and not row["live"] and not allow_offline_failover:
                continue
            failover_order.append({"provider": row["provider"], "model": row["default_model"]})

    payload: dict[str, Any] = {
        "schema_version": MODEL_ROUTING_SCHEMA_VERSION,
        "status": route_status,
        "task_type": task,
        "constraints": {
            "preferred_provider": preferred,
            "preferred_model": preferred_model,
            "allowed_providers": allowed,
            "require_live": require_live,
            "allow_live": allow_live,
            "require_offline": require_offline,
            "require_citations": require_citations,
            "required_modalities": modalities_needed,
            "min_context_tokens": min_context_tokens,
            "system_offline": system_offline,
            "allow_offline_failover": allow_offline_failover,
        },
        "selected_provider": selected_provider,
        "selected_model": selected_model,
        "selection_reason": selection_reason,
        "candidates": candidates,
        "failover_order": failover_order,
        "invariants": {
            "selected_provider_is_allowed": selected_provider is None or selected_provider in allowed,
            "no_implicit_live_escalation": selected is None or not selected["live"] or allow_live,
            "offline_constraint_honored": not require_offline or selected is None or not selected["live"],
            "live_constraint_honored": not require_live or selected is None or selected["live"],
            "selected_provider_ready": selected is None or selected["provider_status"] == "MODEL_PROVIDER_READY",
            "no_remote_provider_while_offline": not system_offline or selected is None or selected.get("locality") != "remote",
            "failover_order_selected_first": selected is None
            or bool(failover_order)
            and failover_order[0]["provider"] == selected_provider,
            "failover_order_within_allowlist": all(row["provider"] in allowed for row in failover_order),
            "no_implicit_offline_failover_from_live": selected is None
            or not selected["live"]
            or allow_offline_failover
            or all(
                next(candidate for candidate in candidates if candidate["provider"] == row["provider"])["live"]
                for row in failover_order
            ),
            "no_implicit_mock_fallback": selected is None
            or selected["provider"] == "mock"
            or allow_offline_failover
            or all(row["provider"] != "mock" for row in failover_order),
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
            "never add an offline fallback behind a live provider without explicit allow_offline_failover=true",
            "never add mock as a fallback behind another provider without explicit allow_offline_failover=true",
        ],
    }
    payload["route_sha256"] = _stable_hash(payload)
    return validate_contract("model_route_decision", payload)
