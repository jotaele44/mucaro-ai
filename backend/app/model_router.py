from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from typing import Any

from .config import Settings, settings
from .permission_engine import PermissionEngineError, PermissionRequest, require


class ModelProviderError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class ModelProviderSpec:
    name: str
    default_model: str
    live: bool
    requires_live_mode: bool
    api_key_env: str | None
    optional_dependency: str | None
    description: str


MODEL_PROVIDER_SPECS: dict[str, ModelProviderSpec] = {
    "mock": ModelProviderSpec(
        name="mock",
        default_model="mock-cited-synthesizer-v1",
        live=False,
        requires_live_mode=False,
        api_key_env=None,
        optional_dependency=None,
        description="Deterministic local cited-answer synthesizer for offline use and tests.",
    ),
    "anthropic": ModelProviderSpec(
        name="anthropic",
        default_model="claude-3-5-sonnet-latest",
        live=True,
        requires_live_mode=True,
        api_key_env="ANTHROPIC_API_KEY",
        optional_dependency="anthropic",
        description="Live Anthropic Messages API adapter, disabled unless live mode and credentials are explicit.",
    ),
}


def classify_provider_exception(exc: BaseException) -> ModelProviderError:
    """Map an SDK/transport exception to a stable ModelProviderError status.

    Matches on class name and status_code so the optional provider SDK is never imported here.
    Interruptions (rate limit, timeout, unavailable) are what recovery and failover act on;
    anything else is a hard, non-retryable failure (bad request, auth, unknown).
    """
    if isinstance(exc, ModelProviderError):
        return exc
    name = type(exc).__name__
    code = getattr(exc, "status_code", None)
    code = code if isinstance(code, int) else None
    detail = f"{name}: {exc}"[:500]
    if name == "RateLimitError" or code == 429:
        return ModelProviderError("ERROR_MODEL_PROVIDER_RATE_LIMIT", detail)
    if isinstance(exc, TimeoutError) or name in {"APITimeoutError", "Timeout", "ReadTimeout", "ConnectTimeout"}:
        return ModelProviderError("ERROR_MODEL_PROVIDER_TIMEOUT", detail)
    if name in {"APIConnectionError", "InternalServerError", "ServiceUnavailableError", "OverloadedError"} or (code is not None and code >= 500):
        return ModelProviderError("ERROR_MODEL_PROVIDER_UNAVAILABLE", detail)
    return ModelProviderError("ERROR_MODEL_PROVIDER_CALL_FAILED", detail)


def normalize_model_provider(model_provider: str | None) -> str:
    provider = (model_provider or "mock").strip().lower()
    if not provider:
        return "mock"
    return provider


def get_model_provider_spec(model_provider: str | None) -> ModelProviderSpec:
    provider = normalize_model_provider(model_provider)
    try:
        return MODEL_PROVIDER_SPECS[provider]
    except KeyError as exc:
        raise ModelProviderError("ERROR_UNSUPPORTED_MODEL_PROVIDER", provider) from exc


def list_model_providers() -> list[dict[str, Any]]:
    return [_provider_status(spec) for spec in MODEL_PROVIDER_SPECS.values()]


def model_provider_names() -> tuple[str, ...]:
    return tuple(MODEL_PROVIDER_SPECS)


def model_provider_status(model_provider: str | None = None) -> dict[str, Any]:
    return _provider_status(get_model_provider_spec(model_provider))


def _provider_status(spec: ModelProviderSpec) -> dict[str, Any]:
    live_mode_enabled = os.getenv("MUCARO_AI_LIVE_MODE") == "1"
    api_key_configured = bool(os.getenv(spec.api_key_env)) if spec.api_key_env else None
    dependency_available = (
        importlib.util.find_spec(spec.optional_dependency) is not None if spec.optional_dependency else None
    )
    warnings: list[str] = []
    status = "MODEL_PROVIDER_READY"
    if spec.requires_live_mode and not live_mode_enabled:
        status = "MODEL_PROVIDER_DISABLED"
        warnings.append("LIVE_MODE_DISABLED")
    elif spec.api_key_env and not api_key_configured:
        status = "MODEL_PROVIDER_NOT_READY"
        warnings.append(f"API_KEY_MISSING:{spec.api_key_env}")
    elif spec.optional_dependency and not dependency_available:
        status = "MODEL_PROVIDER_NOT_READY"
        warnings.append(f"OPTIONAL_DEPENDENCY_MISSING:{spec.optional_dependency}")
    return {
        "name": spec.name,
        "default_model": spec.default_model,
        "live": spec.live,
        "requires_live_mode": spec.requires_live_mode,
        "api_key_env": spec.api_key_env,
        "optional_dependency": spec.optional_dependency,
        "description": spec.description,
        "live_mode_enabled": live_mode_enabled,
        "api_key_configured": api_key_configured,
        "dependency_available": dependency_available,
        "status": status,
        "warnings": warnings,
    }


def generate_answer(
    query: str,
    retrieved_chunks: list[dict[str, Any]],
    *,
    model_provider: str | None = None,
    model_name: str | None = None,
    allow_mock_fallback: bool = False,
    conversation_context: list[dict[str, Any]] | None = None,
    memory_context: list[dict[str, Any]] | None = None,
    cfg: Settings = settings,
) -> dict[str, Any]:
    spec = get_model_provider_spec(model_provider)
    if spec.name == "mock":
        return _mock_answer(
            query,
            retrieved_chunks,
            model_name=model_name or spec.default_model,
            conversation_context=conversation_context or [],
            memory_context=memory_context or [],
        )
    if spec.name == "anthropic" and os.getenv("MUCARO_AI_LIVE_MODE") != "1":
        return _fallback_or_error(
            query,
            retrieved_chunks,
            allow_mock_fallback=allow_mock_fallback,
            status="ERROR_LIVE_MODE_DISABLED",
            detail="Set MUCARO_AI_LIVE_MODE=1 to permit live Anthropic calls.",
            conversation_context=conversation_context or [],
            memory_context=memory_context or [],
        )
    if spec.name == "anthropic" and spec.api_key_env and not os.getenv(spec.api_key_env):
        return _fallback_or_error(
            query,
            retrieved_chunks,
            allow_mock_fallback=allow_mock_fallback,
            status="ERROR_MODEL_API_KEY_MISSING",
            detail="Set ANTHROPIC_API_KEY before using model_provider=anthropic.",
            conversation_context=conversation_context or [],
            memory_context=memory_context or [],
        )
    try:
        require(PermissionRequest(capability="model.invoke", subject=spec.name, target=model_name, origin="operator"), cfg=cfg)
    except PermissionEngineError as exc:
        raise ModelProviderError(exc.status, exc.detail) from exc
    return _anthropic_answer(
        query,
        retrieved_chunks,
        model_name=model_name,
        allow_mock_fallback=allow_mock_fallback,
        conversation_context=conversation_context or [],
        memory_context=memory_context or [],
    )


def _fallback_or_error(
    query: str,
    retrieved_chunks: list[dict[str, Any]],
    *,
    allow_mock_fallback: bool,
    status: str,
    detail: str,
    conversation_context: list[dict[str, Any]],
    memory_context: list[dict[str, Any]],
) -> dict[str, Any]:
    if allow_mock_fallback:
        answer = _mock_answer(
            query,
            retrieved_chunks,
            model_name=f"mock-fallback-{status.lower()}",
            conversation_context=conversation_context,
            memory_context=memory_context,
        )
        answer["provider_warning"] = status
        return answer
    raise ModelProviderError(status, detail)


def _format_conversation_context(conversation_context: list[dict[str, Any]]) -> list[str]:
    lines = []
    for turn in conversation_context:
        role = str(turn.get("role", "unknown"))
        content = " ".join(str(turn.get("content", "")).split())[:320]
        if content:
            lines.append(f"- {role}: {content}")
    return lines


def _format_memory_context(memory_context: list[dict[str, Any]]) -> list[str]:
    lines = []
    for item in memory_context:
        kind = str(item.get("kind", "memory"))
        content = " ".join(str(item.get("content", "")).split())[:320]
        if content:
            lines.append(f"- {kind}: {content}")
    return lines


def _mock_answer(
    query: str,
    retrieved_chunks: list[dict[str, Any]],
    *,
    model_name: str,
    conversation_context: list[dict[str, Any]],
    memory_context: list[dict[str, Any]],
) -> dict[str, Any]:
    if not retrieved_chunks:
        return {
            "model_provider": "mock",
            "model_name": model_name,
            "answer": "ERROR_NO_RETRIEVAL_CONTEXT: No retrieved source chunks were available for this query.",
            "cited_chunk_ids": [],
        }
    cited = retrieved_chunks[: min(3, len(retrieved_chunks))]
    source_lines = []
    for idx, chunk in enumerate(cited, start=1):
        snippet = " ".join(str(chunk.get("text", "")).split())[:240]
        source_lines.append(f"[{idx}] {snippet} (chunk_id={chunk['chunk_id']})")
    context_lines = _format_conversation_context(conversation_context)
    memory_lines = _format_memory_context(memory_context)
    answer = "\n".join(
        [
            f"Mock cited answer for query: {query}",
            f"Conversation context turns used: {len(context_lines)}",
            f"Memory items used: {len(memory_lines)}",
            *(["Conversation context:", *context_lines] if context_lines else []),
            *(["Memory context:", *memory_lines] if memory_lines else []),
            "Evidence summary:",
            *source_lines,
            "Validation note: mock mode uses retrieved text directly and requires cited chunk IDs.",
        ]
    )
    return {
        "model_provider": "mock",
        "model_name": model_name,
        "answer": answer,
        "cited_chunk_ids": [c["chunk_id"] for c in cited],
    }


def _anthropic_answer(
    query: str,
    retrieved_chunks: list[dict[str, Any]],
    *,
    model_name: str | None = None,
    allow_mock_fallback: bool,
    conversation_context: list[dict[str, Any]],
    memory_context: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        from anthropic import Anthropic  # type: ignore
    except Exception:
        return _fallback_or_error(
            query,
            retrieved_chunks,
            allow_mock_fallback=allow_mock_fallback,
            status="ERROR_MODEL_SDK_MISSING",
            detail='Install the optional dependency group with: python -m pip install -e ".[anthropic]"',
            conversation_context=conversation_context,
            memory_context=memory_context,
        )
    client = Anthropic()
    context = "\n\n".join(
        f"chunk_id={c['chunk_id']} source_id={c['source_id']}\n{c['text']}" for c in retrieved_chunks[:5]
    )
    prompt = (
        "Answer using only the provided context. Cite chunk IDs explicitly. "
        "Use conversation history only to interpret the user's follow-up, not as source evidence. "
        "Use memory only for user preferences or durable profile context, not as source evidence. "
        "If context is insufficient, say so.\n\n"
        f"Conversation history:\n{chr(10).join(_format_conversation_context(conversation_context)) or 'none'}\n\n"
        f"Memory:\n{chr(10).join(_format_memory_context(memory_context)) or 'none'}\n\n"
        f"Question: {query}\n\nContext:\n{context}"
    )
    try:
        response = client.messages.create(
            model=model_name or os.getenv("MUCARO_AI_MODEL", MODEL_PROVIDER_SPECS["anthropic"].default_model),
            max_tokens=900,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # SDK/transport errors become stable statuses so recovery can act on them
        raise classify_provider_exception(exc) from exc
    text = "\n".join(block.text for block in getattr(response, "content", None) or [] if getattr(block, "type", None) == "text")
    if not text.strip():
        raise ModelProviderError("ERROR_MODEL_PROVIDER_MALFORMED_RESPONSE", "provider returned no text content")
    cited = [c["chunk_id"] for c in retrieved_chunks if c["chunk_id"] in text]
    return {
        "model_provider": "anthropic",
        "model_name": model_name or os.getenv("MUCARO_AI_MODEL", MODEL_PROVIDER_SPECS["anthropic"].default_model),
        "answer": text,
        "cited_chunk_ids": cited,
    }
