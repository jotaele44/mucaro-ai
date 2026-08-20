from __future__ import annotations

import os
from typing import Any


def generate_answer(query: str, retrieved_chunks: list[dict[str, Any]], *, model_provider: str | None = None, model_name: str | None = None) -> dict[str, Any]:
    provider = model_provider or ("anthropic" if os.getenv("ANTHROPIC_API_KEY") else "mock")
    if provider == "anthropic" and os.getenv("ANTHROPIC_API_KEY"):
        # Safe fallback: keep v1 deterministic unless anthropic SDK is available and explicitly enabled.
        if os.getenv("MUCARO_AI_LIVE_MODE") == "1":
            return _anthropic_answer(query, retrieved_chunks, model_name=model_name)
    return _mock_answer(query, retrieved_chunks, model_name=model_name or "mock-cited-synthesizer-v1")


def _mock_answer(query: str, retrieved_chunks: list[dict[str, Any]], *, model_name: str) -> dict[str, Any]:
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
    answer = "\n".join(
        [
            f"Mock cited answer for query: {query}",
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


def _anthropic_answer(query: str, retrieved_chunks: list[dict[str, Any]], *, model_name: str | None = None) -> dict[str, Any]:
    try:
        from anthropic import Anthropic  # type: ignore
    except Exception:
        return _mock_answer(query, retrieved_chunks, model_name="mock-fallback-anthropic-sdk-missing")
    client = Anthropic()
    context = "\n\n".join(
        f"chunk_id={c['chunk_id']} source_id={c['source_id']}\n{c['text']}" for c in retrieved_chunks[:5]
    )
    prompt = (
        "Answer using only the provided context. Cite chunk IDs explicitly. "
        "If context is insufficient, say so.\n\n"
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
