"""Fail-closed, provider-neutral request envelope. No model invocation occurs here."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

class ContractError(ValueError):
    pass

@dataclass(frozen=True)
class ModelCapabilities:
    tool_calls: bool = False
    streaming: bool = False
    structured_output: bool = False
    context_tokens: int = 8192

@dataclass(frozen=True)
class ModelRequest:
    provider: str
    model: str
    messages: tuple[tuple[str, str], ...]
    locality: Literal["local", "remote"]
    allow_remote: bool = False
    tools_requested: bool = False
    stream_requested: bool = False
    structured_requested: bool = False
    max_output_tokens: int = 512

def validate_request(request: ModelRequest, capabilities: ModelCapabilities) -> ModelRequest:
    """Reject malformed runtime values before dispatch; dataclass hints are not runtime checks."""
    if not isinstance(request, ModelRequest) or not isinstance(capabilities, ModelCapabilities):
        raise ContractError("INVALID_CONTRACT_TYPES")
    if (
        not isinstance(request.provider, str) or not request.provider.strip()
        or not isinstance(request.model, str) or not request.model.strip()
    ):
        raise ContractError("MODEL_IDENTITY_REQUIRED")
    if request.locality not in ("local", "remote"):
        raise ContractError("INVALID_LOCALITY")
    if type(request.allow_remote) is not bool or any(
        type(flag) is not bool
        for flag in (request.tools_requested, request.stream_requested, request.structured_requested)
    ):
        raise ContractError("INVALID_FLAGS")
    if request.locality == "remote" and not request.allow_remote:
        raise ContractError("REMOTE_REQUIRES_OPT_IN")
    if not isinstance(request.messages, tuple) or not request.messages or any(
        not isinstance(message, tuple) or len(message) != 2
        or not isinstance(message[0], str) or message[0] not in ("system", "user", "assistant", "tool")
        or not isinstance(message[1], str) or not message[1].strip()
        for message in request.messages
    ):
        raise ContractError("INVALID_MESSAGES")
    if (
        type(capabilities.context_tokens) is not int or type(request.max_output_tokens) is not int
        or capabilities.context_tokens <= 0 or request.max_output_tokens <= 0
        or request.max_output_tokens >= capabilities.context_tokens
    ):
        raise ContractError("INVALID_TOKEN_BUDGET")
    if any(type(flag) is not bool for flag in (
        capabilities.tool_calls, capabilities.streaming, capabilities.structured_output
    )):
        raise ContractError("INVALID_CAPABILITIES")
    if request.tools_requested and not capabilities.tool_calls:
        raise ContractError("TOOL_CALLS_UNSUPPORTED")
    if request.stream_requested and not capabilities.streaming:
        raise ContractError("STREAMING_UNSUPPORTED")
    if request.structured_requested and not capabilities.structured_output:
        raise ContractError("STRUCTURED_OUTPUT_UNSUPPORTED")
    return request
