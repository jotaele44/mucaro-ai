from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .citations import build_citation_records, score_confidence, validate_citations
from .config import Settings, settings
from .db import ensure_db, upsert_payload
from .exports import export_answer_trace
from .ingestion import get_chunks
from .injection_guard import scan_chunks, scan_text
from .memory_policy import MemorySelectionError, select_memory_context
from .model_failover import FailoverPolicy, run_with_failover
from .model_router import ModelProviderError, generate_answer
from .model_routing_policy import ModelRoutingError, plan_model_route
from .provider_recovery import (
    ProviderRecoveryError,
    execute_with_provider_checkpoint,
    pause_provider_checkpoint,
)
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
    allow_live: bool = False,
    require_offline: bool = False,
    conversation_context: list[dict[str, Any]] | None = None,
    memory_context: list[dict[str, Any]] | None = None,
    cfg: Settings = settings,
    persist: bool = True,
    on_text_delta: Callable[[str], None] | None = None,
    checkpoint_id: str | None = None,
    provider_operation_key: str | None = None,
    failover_policy: FailoverPolicy | None = None,
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
    ensure_db(cfg.sqlite_path)
    retrieval_started = time.perf_counter()
    retrieval = retrieve(normalized_query, get_chunks(cfg), top_k=top_k, strategy=retrieval_strategy, cfg=cfg)
    retrieval_ms = _elapsed_ms(retrieval_started)
    if retrieval["status"].startswith("ERROR_"):
        return retrieval
    retrieved = retrieval["retrieved_chunks"]
    try:
        memory_selection = select_memory_context(cfg, items=memory_context)
    except MemorySelectionError as exc:
        return {
            "status": exc.status,
            "query": normalized_query,
            "retrieved_chunks": retrieved,
            "detail": exc.detail,
        }
    selected_memory_context = memory_selection["selected"]

    preferred_provider = model_provider or cfg.default_model_provider
    try:
        model_route = plan_model_route(
            task_type="cited_qa",
            preferred_provider=preferred_provider,
            preferred_model=model_name,
            allow_live=allow_live,
            require_offline=require_offline,
            require_citations=True,
            allow_offline_failover=allow_mock_fallback,
        )
    except ModelRoutingError as exc:
        if not allow_mock_fallback:
            return {
                "status": exc.status,
                "query": normalized_query,
                "retrieved_chunks": retrieved,
                "detail": exc.detail,
            }
        model_route = plan_model_route(
            task_type="cited_qa",
            preferred_provider="mock",
            allowed_providers=["mock"],
            allow_live=False,
            require_offline=True,
            require_citations=True,
        )
        model_route["fallback_reason"] = exc.status

    if model_route["status"] != "MODEL_ROUTE_READY":
        if allow_mock_fallback and model_route.get("selected_provider") != "mock":
            model_route = plan_model_route(
                task_type="cited_qa",
                preferred_provider="mock",
                allowed_providers=["mock"],
                allow_live=False,
                require_offline=True,
                require_citations=True,
            )
            model_route["fallback_reason"] = "PRIMARY_ROUTE_BLOCKED"
        else:
            return {
                "status": "ERROR_MODEL_ROUTE_BLOCKED",
                "query": normalized_query,
                "retrieved_chunks": retrieved,
                "detail": model_route.get("selection_reason"),
                "model_route": model_route,
            }

    failover_order = model_route.get("failover_order") or [
        {"provider": model_route["selected_provider"], "model": model_route["selected_model"]}
    ]
    streaming_suppressed = on_text_delta is not None and len(failover_order) > 1
    model_started = time.perf_counter()
    first_token_ms: list[float] = []

    def timed_text_delta(text: str) -> None:
        if not first_token_ms:
            first_token_ms.append(_elapsed_ms(model_started))
        on_text_delta(text)  # type: ignore[misc]

    stream_callback = None if streaming_suppressed or on_text_delta is None else timed_text_delta
    operation_key = provider_operation_key or stable_id(
        "model-op",
        normalized_query,
        model_route["route_sha256"],
        *[str(chunk["chunk_id"]) for chunk in retrieved],
        length=24,
    )

    def invoke(provider: str, model_id: str | None) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            return generate_answer(
                normalized_query,
                retrieved,
                model_provider=provider,
                model_name=model_id,
                allow_mock_fallback=False,
                conversation_context=conversation_context or [],
                memory_context=selected_memory_context,
                on_text_delta=stream_callback,
                cfg=cfg,
            )

        if checkpoint_id is None:
            return operation()
        try:
            execution = execute_with_provider_checkpoint(
                checkpoint_id,
                provider=provider,
                model=model_id,
                operation=operation,
                operation_key=operation_key,
                pause_on_interruption=False,
                cfg=cfg,
            )
        except ProviderRecoveryError as exc:
            raise ModelProviderError(exc.status, exc.detail) from exc
        if execution["status"] == "PROVIDER_EXECUTION_INTERRUPTED":
            raise ModelProviderError(
                str(execution["provider_error"]),
                str(execution.get("detail") or execution["provider_error"]),
            )
        if execution["status"] in {"PROVIDER_EXECUTION_OK", "PROVIDER_EXECUTION_ALREADY_ACCEPTED"}:
            result = execution.get("result")
            if not isinstance(result, dict):
                raise ModelProviderError("ERROR_PROVIDER_RESULT_INVALID", "accepted provider result is not an object")
            return result
        raise ModelProviderError("ERROR_PROVIDER_EXECUTION_STATE", str(execution["status"]))

    failover = run_with_failover(
        failover_order,
        invoke,
        policy=failover_policy,
    )
    if failover["status"] != "FAILOVER_OK":
        failure = {
            "status": failover.get("error_status") or "ERROR_MODEL_ROUTE_BLOCKED",
            "query": normalized_query,
            "retrieved_chunks": retrieved,
            "detail": failover.get("detail", ""),
            "model_route": model_route,
            "model_attempts": failover["attempts"],
        }
        if failover.get("interruption") and checkpoint_id:
            last = failover["attempts"][-1]
            try:
                paused = pause_provider_checkpoint(
                    checkpoint_id,
                    provider=last["provider"],
                    model=last["model"],
                    provider_error=str(failover["error_status"]),
                    cfg=cfg,
                )
            except ProviderRecoveryError as exc:
                return {**failure, "checkpoint_error": exc.status}
            return {
                **failure,
                "status": "PAUSED_PROVIDER",
                "provider_error": failover["error_status"],
                "checkpoint": paused,
            }
        return failure
    model = failover["result"]
    model_ms = _elapsed_ms(model_started)
    # Advisory only: instruction-like text in sources or output is surfaced, never acted on (TGT-022).
    source_signals = scan_chunks(retrieved)
    signals_by_chunk = {row["chunk_id"]: row["signals"] for row in source_signals}
    for chunk in retrieved:
        if chunk.get("chunk_id") in signals_by_chunk:
            chunk["injection_signals"] = signals_by_chunk[chunk["chunk_id"]]
    output_scan = scan_text(str(model.get("answer") or ""))
    retrieved_ids = [chunk["chunk_id"] for chunk in retrieved]
    cited_ids = model.get("cited_chunk_ids", [])
    cited_chunks = build_citation_records(retrieved, cited_ids)
    source_ids = [record["source_id"] for record in cited_chunks]
    answer_trace = {
        "run_id": stable_id("run", normalized_query, utc_now(), length=12),
        "query": normalized_query,
        "model_provider": model["model_provider"],
        "model_name": model["model_name"],
        "prompt_template_id": "default_cited_rag_with_history_v1" if conversation_context else "default_cited_rag_v1",
        "conversation_context_turn_count": len(conversation_context or []),
        "memory_context_count": len(selected_memory_context),
        "memory_selection_sha256": memory_selection["selection_sha256"],
        "memory_excluded_count": memory_selection["excluded_count"],
        "memory_ids": [
            str(item.get("memory_id"))
            for item in selected_memory_context
            if item.get("memory_id")
        ],
        "model_route_sha256": model_route["route_sha256"],
        "model_route_selection_reason": model_route.get("selection_reason"),
        "model_route_fallback_reason": model_route.get("fallback_reason"),
        "model_attempts": failover["attempts"],
        "model_failed_over": bool(failover.get("failed_over")),
        "model_failover_streaming_suppressed": streaming_suppressed,
        "retrieval_strategy": retrieved[0].get("retrieval_strategy", "unknown") if retrieved else "none",
        "retrieved_chunk_ids": retrieved_ids,
        "cited_chunk_ids": cited_ids,
        "cited_chunks": cited_chunks,
        "source_ids": source_ids,
        "answer": model["answer"],
        "answer_streamed": bool(model.get("streamed")),
        "confidence": score_confidence(cited_ids, source_ids, bool(retrieved)),
        "validation_flags": (["WARN_UNTRUSTED_INSTRUCTIONS_IN_SOURCES"] if source_signals else [])
        + (["WARN_MODEL_OUTPUT_CONTAINS_INSTRUCTIONS"] if output_scan["suspected"] else [])
        + (["WARN_MODEL_OUTPUT_TRUNCATED"] if model.get("stop_reason") == "max_tokens" else [])
        + (["WARN_MODEL_REFUSAL"] if model.get("stop_reason") == "refusal" else []),
        "untrusted_content_signals": source_signals,
        "model_output_signals": output_scan["signals"],
        "model_stop_reason": model.get("stop_reason"),
        "model_effort": model.get("effort"),
        # Time to the first streamed token; providers that do not stream deliver everything at model_ms.
        "timings_ms": {
            "retrieval_ms": retrieval_ms,
            "model_first_token_ms": first_token_ms[0] if first_token_ms else model_ms,
            "model_ms": model_ms,
        },
        "created_at": utc_now(),
    }
    answer_trace = validate_citations(answer_trace)
    answer_trace["export_paths"] = export_answer_trace(answer_trace, cfg)
    if persist:
        upsert_payload("runs", "run_id", answer_trace, cfg.sqlite_path)
    return answer_trace


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
