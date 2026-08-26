from __future__ import annotations

import os
from typing import Any


class ModelProviderError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def generate_answer(
    query: str,
    retrieved_chunks: list[dict[str, Any]],
    *,
    model_provider: str | None = None,
    model_name: str | None = None,
    allow_mock_fallback: bool = False,
    conversation_context: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    provider = model_provider or "mock"
    if provider == "mock":
        return _mock_answer(
            query,
            retrieved_chunks,
            model_name=model_name or "mock-cited-synthesizer-v1",
            conversation_context=conversation_context or [],
        )
    if provider != "anthropic":
        raise ModelProviderError("ERROR_UNSUPPORTED_MODEL_PROVIDER", provider)
    if os.getenv("MUCARO_AI_LIVE_MODE") != "1":
        return _fallback_or_error(
            query,
            retrieved_chunks,
            allow_mock_fallback=allow_mock_fallback,
            status="ERROR_LIVE_MODE_DISABLED",
            detail="Set MUCARO_AI_LIVE_MODE=1 to permit live Anthropic calls.",
            conversation_context=conversation_context or [],
        )
    if not os.getenv("ANTHROPIC_API_KEY"):
        return _fallback_or_error(
            query,
            retrieved_chunks,
            allow_mock_fallback=allow_mock_fallback,
            status="ERROR_MODEL_API_KEY_MISSING",
            detail="Set ANTHROPIC_API_KEY before using model_provider=anthropic.",
            conversation_context=conversation_context or [],
        )
    return _anthropic_answer(
        query,
        retrieved_chunks,
        model_name=model_name,
        allow_mock_fallback=allow_mock_fallback,
        conversation_context=conversation_context or [],
    )


def _fallback_or_error(
    query: str,
    retrieved_chunks: list[dict[str, Any]],
    *,
    allow_mock_fallback: bool,
    status: str,
    detail: str,
    conversation_context: list[dict[str, Any]],
) -> dict[str, Any]:
    if allow_mock_fallback:
        answer = _mock_answer(
            query,
            retrieved_chunks,
            model_name=f"mock-fallback-{status.lower()}",
            conversation_context=conversation_context,
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


def _mock_answer(
    query: str,
    retrieved_chunks: list[dict[str, Any]],
    *,
    model_name: str,
    conversation_context: list[dict[str, Any]],
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
    answer = "\n".join(
        [
            f"Mock cited answer for query: {query}",
            f"Conversation context turns used: {len(context_lines)}",
            *(["Conversation context:", *context_lines] if context_lines else []),
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
        )
    client = Anthropic()
    context = "\n\n".join(
        f"chunk_id={c['chunk_id']} source_id={c['source_id']}\n{c['text']}" for c in retrieved_chunks[:5]
    )
    prompt = (
        "Answer using only the provided context. Cite chunk IDs explicitly. "
        "Use conversation history only to interpret the user's follow-up, not as source evidence. "
        "If context is insufficient, say so.\n\n"
        f"Conversation history:\n{chr(10).join(_format_conversation_context(conversation_context)) or 'none'}\n\n"
        f"Question: {query}\n\nContext:\n{context}"
    )
    response = client.messages.create(
        model=model_name or os.getenv("MUCARO_AI_MODEL", "claude-3-5-sonnet-latest"),
        max_tokens=900,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "\n".join(block.text for block in response.content if getattr(block, "type", None) == "text")
    cited = [c["chunk_id"] for c in retrieved_chunks if c["chunk_id"] in text]
    return {
        "model_provider": "anthropic",
        "model_name": model_name or os.getenv("MUCARO_AI_MODEL", "claude-3-5-sonnet-latest"),
        "answer": text,
        "cited_chunk_ids": cited,
    }
