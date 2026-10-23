"""Provider-neutral validation for model-generated tool proposals.

Provider response formats are untrusted input.  This module normalizes only
the structured tool-call portion of a response; it never executes a tool or
turns provider text into an instruction.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .tools import TOOL_REGISTRY

MAX_PROVIDER_TOOL_PROPOSALS = 4
PROVIDER_TOOL_PROPOSAL_SCHEMA = "mucaro-agent-tool-proposals/1.0"


class AgentToolProposalError(ValueError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _read(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def provider_tool_schemas() -> list[dict[str, Any]]:
    """Expose the local allowlist as provider tool definitions.

    Argument schemas remain intentionally open at this boundary because the
    existing tool handlers own their domain-specific validation.  The runtime
    still requires a JSON object and revalidates the call before execution.
    """
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": {"type": "object", "additionalProperties": True},
        }
        for tool in TOOL_REGISTRY.values()
    ]


def openai_tool_schemas() -> list[dict[str, Any]]:
    """Return the same allowlist in OpenAI-compatible function-call shape."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": {"type": "object", "additionalProperties": True},
            },
        }
        for tool in TOOL_REGISTRY.values()
    ]


def _anthropic_candidates(payload: Any) -> list[dict[str, Any]]:
    content = _read(payload, "content")
    if not isinstance(content, list):
        raise AgentToolProposalError("ERROR_AGENT_PROVIDER_RESPONSE_INVALID", "Anthropic response content must be an array.")
    candidates: list[dict[str, Any]] = []
    for block in content:
        if _read(block, "type") != "tool_use":
            continue
        candidates.append(
            {
                "provider_call_id": _read(block, "id"),
                "tool": _read(block, "name"),
                "arguments": _read(block, "input"),
            }
        )
    return candidates


def _openai_candidates(payload: Any) -> list[dict[str, Any]]:
    choices = _read(payload, "choices")
    if not isinstance(choices, list) or not choices:
        raise AgentToolProposalError("ERROR_AGENT_PROVIDER_RESPONSE_INVALID", "OpenAI-compatible response choices are missing.")
    message = _read(choices[0], "message")
    calls = _read(message, "tool_calls")
    if calls is None:
        return []
    if not isinstance(calls, list):
        raise AgentToolProposalError("ERROR_AGENT_PROVIDER_RESPONSE_INVALID", "OpenAI-compatible tool_calls must be an array.")
    candidates: list[dict[str, Any]] = []
    for call in calls:
        function = _read(call, "function")
        raw_arguments = _read(function, "arguments")
        if isinstance(raw_arguments, str):
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                raise AgentToolProposalError(
                    "ERROR_AGENT_TOOL_ARGUMENTS_INVALID",
                    f"OpenAI-compatible tool arguments are not valid JSON: {exc.msg}.",
                ) from exc
        else:
            arguments = raw_arguments
        candidates.append(
            {
                "provider_call_id": _read(call, "id"),
                "tool": _read(function, "name"),
                "arguments": arguments,
            }
        )
    return candidates


def validate_provider_tool_proposals(
    candidates: list[dict[str, Any]],
    *,
    provider: str,
    model: str,
    response_id: str | None = None,
    max_actions: int = MAX_PROVIDER_TOOL_PROPOSALS,
) -> dict[str, Any]:
    """Validate normalized provider calls without executing them."""
    if not isinstance(candidates, list):
        raise AgentToolProposalError("ERROR_AGENT_TOOL_PROPOSALS_INVALID", "Provider tool proposals must be an array.")
    if max_actions < 1 or max_actions > MAX_PROVIDER_TOOL_PROPOSALS:
        raise AgentToolProposalError("ERROR_AGENT_TOOL_PROPOSAL_LIMIT_INVALID", "Provider proposal limit is outside the bounded maximum.")
    if len(candidates) > max_actions:
        raise AgentToolProposalError(
            "ERROR_AGENT_TOOL_PROPOSAL_LIMIT_EXCEEDED",
            f"Provider proposed {len(candidates)} actions; the bounded limit is {max_actions}.",
        )

    proposals: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_calls: set[str] = set()
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise AgentToolProposalError("ERROR_AGENT_TOOL_PROPOSAL_INVALID", f"Proposal {index} must be an object.")
        call_id = candidate.get("provider_call_id")
        tool_name = candidate.get("tool")
        arguments = candidate.get("arguments")
        if not isinstance(call_id, str) or not call_id.strip():
            raise AgentToolProposalError("ERROR_AGENT_TOOL_CALL_ID_MISSING", f"Proposal {index} has no provider call id.")
        if call_id in seen_ids:
            raise AgentToolProposalError("ERROR_AGENT_TOOL_CALL_ID_DUPLICATE", f"Provider call id is duplicated: {call_id}.")
        if not isinstance(tool_name, str) or tool_name not in TOOL_REGISTRY:
            raise AgentToolProposalError("ERROR_AGENT_TOOL_NOT_ALLOWED", f"Provider proposed a tool outside the local allowlist: {tool_name!r}.")
        if not isinstance(arguments, dict):
            raise AgentToolProposalError("ERROR_AGENT_TOOL_ARGUMENTS_NOT_OBJECT", f"Arguments for {tool_name} must be a JSON object.")
        call_key = _canonical({"tool": tool_name, "arguments": arguments})
        if call_key in seen_calls:
            raise AgentToolProposalError("ERROR_AGENT_TOOL_PROPOSAL_DUPLICATE", f"Provider proposed the same call more than once: {tool_name}.")
        seen_ids.add(call_id)
        seen_calls.add(call_key)
        proposals.append(
            {
                "provider_call_id": call_id,
                "tool": tool_name,
                "arguments": arguments,
                "arguments_sha256": _sha256(arguments),
            }
        )

    body = {
        "schema_version": PROVIDER_TOOL_PROPOSAL_SCHEMA,
        "status": "AGENT_NO_TOOL_PROPOSALS" if not proposals else "AGENT_TOOL_PROPOSALS_VALID",
        "provider": provider,
        "model": model,
        "response_id": response_id,
        "proposals": proposals,
        "counts": {"selected_actions": len(proposals), "max_actions": max_actions},
        "invariants": {
            "never_executes_tools": True,
            "allowlist_validated": all(row["tool"] in TOOL_REGISTRY for row in proposals),
            "arguments_are_objects": all(isinstance(row["arguments"], dict) for row in proposals),
            "provider_call_ids_unique": len(seen_ids) == len(proposals),
            "duplicate_calls_rejected": True,
            "actions_are_bounded": len(proposals) <= max_actions,
        },
    }
    body["proposal_sha256"] = _sha256(body)
    return body


def parse_provider_tool_proposals(
    provider: str,
    payload: Any,
    *,
    model: str,
    response_id: str | None = None,
    max_actions: int = MAX_PROVIDER_TOOL_PROPOSALS,
) -> dict[str, Any]:
    """Parse one provider response shape, then apply the common validator."""
    if provider == "anthropic":
        candidates = _anthropic_candidates(payload)
    elif provider == "openai_compatible_local":
        candidates = _openai_candidates(payload)
    else:
        raise AgentToolProposalError("ERROR_AGENT_PROVIDER_UNSUPPORTED", f"Provider has no structured tool adapter: {provider}.")
    return validate_provider_tool_proposals(
        candidates,
        provider=provider,
        model=model,
        response_id=response_id,
        max_actions=max_actions,
    )
