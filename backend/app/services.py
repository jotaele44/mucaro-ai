from __future__ import annotations

from typing import Any

from .citations import score_confidence, validate_citations
from .config import Settings, settings
from .db import init_db, upsert_payload
from .exports import export_answer_trace
from .ingestion import get_chunks
from .memory import active_memory_context
from .model_router import ModelProviderError, generate_answer
from .retrieval import retrieve
from .utils import stable_id, utc_now


MIN_TOP_K = 1
MAX_TOP_K = 20


def answer_question(
    query: str,
    *,
    top_k: int = 5,
    retrieval_strategy: str = "hybrid",
    model_provider: str | None = None,
    model_name: str | None = None,
    allow_mock_fallback: bool = False,
    conversation_context: list[dict[str, Any]] | None = None,
    memory_context: list[dict[str, Any]] | None = None,
    cfg: Settings = settings,
    persist: bool = True,
) -> dict[str, Any]:
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("EMPTY_QUERY")
    if top_k < MIN_TOP_K or top_k > MAX_TOP_K:
        return {
            "status": "ERROR_INVALID_TOP_K",
            "query": normalized_query,
            "retrieved_chunks": [],
            "warnings": [f"TOP_K_RANGE:{MIN_TOP_K}-{MAX_TOP_K}"],
        }
    cfg.ensure_dirs()
    init_db(cfg.sqlite_path)
    retrieval = retrieve(normalized_query, get_chunks(cfg), top_k=top_k, strategy=retrieval_strategy, cfg=cfg)
    if retrieval["status"].startswith("ERROR_"):
        return retrieval
    retrieved = retrieval["retrieved_chunks"]
    selected_memory_context = memory_context if memory_context is not None else active_memory_context(cfg)
    try:
        model = generate_answer(
            normalized_query,
            retrieved,
            model_provider=model_provider,
            model_name=model_name,
            allow_mock_fallback=allow_mock_fallback,
            conversation_context=conversation_context or [],
            memory_context=selected_memory_context,
        )
    except ModelProviderError as exc:
        return {
            "status": exc.status,
            "query": normalized_query,
            "retrieved_chunks": retrieved,
            "detail": exc.detail,
        }
    retrieved_ids = [chunk["chunk_id"] for chunk in retrieved]
    cited_ids = model.get("cited_chunk_ids", [])
    source_ids = [chunk["source_id"] for chunk in retrieved if chunk["chunk_id"] in cited_ids]
    answer_trace = {
        "run_id": stable_id("run", normalized_query, utc_now(), length=12),
        "query": normalized_query,
        "model_provider": model["model_provider"],
        "model_name": model["model_name"],
        "prompt_template_id": "default_cited_rag_with_history_v1" if conversation_context else "default_cited_rag_v1",
        "conversation_context_turn_count": len(conversation_context or []),
        "memory_context_count": len(selected_memory_context),
        "memory_ids": [
            str(item.get("memory_id"))
            for item in selected_memory_context
            if item.get("memory_id")
        ],
        "retrieval_strategy": retrieved[0].get("retrieval_strategy", "unknown") if retrieved else "none",
        "retrieved_chunk_ids": retrieved_ids,
        "cited_chunk_ids": cited_ids,
        "source_ids": source_ids,
        "answer": model["answer"],
        "confidence": score_confidence(cited_ids, source_ids, bool(retrieved)),
        "validation_flags": [],
        "created_at": utc_now(),
    }
    answer_trace = validate_citations(answer_trace)
    answer_trace["export_paths"] = export_answer_trace(answer_trace, cfg)
    if persist:
        upsert_payload("runs", "run_id", answer_trace, cfg.sqlite_path)
    return answer_trace
