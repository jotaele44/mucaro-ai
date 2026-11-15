from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .citations import NO_SOURCES_USED, build_citation_records, score_confidence, validate_citations
from .config import Settings, settings
from .db import ensure_db, upsert_payload
from .exports import export_answer_trace
from .ingestion import get_chunks
from .injection_guard import scan_chunks, scan_text
from .memory_policy import MemorySelectionError, select_memory_context
from .model_failover import FailoverPolicy, run_with_failover
from .model_router import ModelProviderError, generate_answer, model_provider_status
from .model_routing_policy import ModelRoutingError, plan_model_route
from .provider_recovery import ProviderRecoveryError, pause_provider_checkpoint
from .retrieval import retrieve
from .utils import stable_id, utc_now

MIN_TOP_K = 1
MAX_TOP_K = 20
# Retrieval outcomes that mean "nothing to cite", as opposed to a broken index or a bad request.
NO_EVIDENCE_STATUSES = frozenset({"ERROR_NO_SOURCES", "ERROR_NO_RETRIEVAL_CONTEXT"})


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
    conversation_scope_id: str | None = None,
    memory_context: list[dict[str, Any]] | None = None,
    cfg: Settings = settings,
    persist: bool = True,
    on_text_delta: Callable[[str], None] | None = None,
    checkpoint_id: str | None = None,
    failover_policy: FailoverPolicy | None = None,
    allow_ungrounded: bool = False,
    answer_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Answer from local sources.

    answer_override carries text an agent loop already produced with the routed model
    ({"answer", "model_name"?, "model_usage"?, "agent"?}); no second model call is made, and the
    text goes through the same retrieval, citation validation, export and persistence.

    allow_ungrounded=True (chat) lets the selected model reply when no source
    matches; that reply is marked NO_SOURCES_USED and never counts as cited.
    The default stays strict: no matching source is an error and no model runs.
    """
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
    ungrounded = allow_ungrounded and retrieval["status"] in NO_EVIDENCE_STATUSES
    if retrieval["status"].startswith("ERROR_") and not ungrounded:
        return retrieval
    retrieved = [] if ungrounded else retrieval["retrieved_chunks"]
    task_type = "conversation" if ungrounded else "cited_qa"
    try:
        memory_selection = select_memory_context(
            cfg,
            items=memory_context,
            conversation_scope_id=conversation_scope_id,
        )
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
            task_type=task_type,
            preferred_provider=preferred_provider,
            preferred_model=model_name,
            allow_live=allow_live,
            require_offline=require_offline,
            require_citations=not ungrounded,
            allow_offline_failover=allow_mock_fallback,
            cfg=cfg,
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
            task_type=task_type,
            preferred_provider="mock",
            allowed_providers=["mock"],
            allow_live=False,
            require_offline=True,
            require_citations=not ungrounded,
            cfg=cfg,
        )
        model_route["fallback_reason"] = exc.status

    if model_route["status"] != "MODEL_ROUTE_READY":
        preferred_candidate = next(
            (
                row
                for row in model_route.get("candidates", [])
                if row.get("provider") == preferred_provider
            ),
            None,
        )
        permission_warning = next(
            (
                str(warning)
                for warning in (preferred_candidate or {}).get("warnings", [])
                if str(warning).startswith("MODEL_INVOKE_PERMISSION:")
            ),
            None,
        )
        if permission_warning is not None:
            permission_status = permission_warning.split(":", 1)[1]
            return {
                "status": permission_status,
                "query": normalized_query,
                "retrieved_chunks": retrieved,
                "detail": "model.invoke permission denied before provider readiness transport",
                "model_route": model_route,
            }
        if allow_mock_fallback and model_route.get("selected_provider") != "mock":
            model_route = plan_model_route(
                task_type=task_type,
                preferred_provider="mock",
                allowed_providers=["mock"],
                allow_live=False,
                require_offline=True,
                require_citations=not ungrounded,
                cfg=cfg,
            )
            model_route["fallback_reason"] = "PRIMARY_ROUTE_BLOCKED"
        else:
            return {
                "status": "ERROR_MODEL_ROUTE_BLOCKED",
                "query": normalized_query,
                "retrieved_chunks": retrieved,
                "detail": describe_blocked_route(model_route, preferred_provider, cfg),
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

    def attempt(provider: str, model_id: str | None) -> dict[str, Any]:
        if answer_override is not None:
            text = str(answer_override["answer"])
            produced: dict[str, Any] = {
                "model_provider": provider,
                "model_name": answer_override.get("model_name") or model_id,
                "answer": text,
                # Only chunks retrieved for this message can be cited; an id the model invented is not one.
                "cited_chunk_ids": [chunk["chunk_id"] for chunk in retrieved if chunk["chunk_id"] in text],
            }
            if answer_override.get("model_usage") is not None:
                produced["model_usage"] = answer_override["model_usage"]
            return produced
        try:
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
                **({"ungrounded": True} if ungrounded else {}),
            )
        except ModelProviderError as exc:
            # Once text reaches the caller, another attempt would duplicate user-visible output.
            exc.partial_output = bool(first_token_ms)
            raise

    failover = run_with_failover(
        failover_order,
        attempt,
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
                    provider_error=failover["error_status"],
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
        "prompt_template_id": (
            ("conversation_no_sources_with_history_v1" if conversation_context else "conversation_no_sources_v1")
            if ungrounded
            else ("default_cited_rag_with_history_v1" if conversation_context else "default_cited_rag_v1")
        ),
        "conversation_context_turn_count": len(conversation_context or []),
        "memory_context_count": len(selected_memory_context),
        "memory_selection_sha256": memory_selection["selection_sha256"],
        "memory_excluded_count": memory_selection["excluded_count"],
        "memory_scope_context": {
            "workspace_scope_id": memory_selection["workspace_scope_id"],
            "conversation_scope_id": memory_selection["conversation_scope_id"],
        },
        "memory_scope_counts": {
            scope: sum(1 for item in selected_memory_context if item.get("scope") == scope)
            for scope in ("global", "workspace", "conversation")
        },
        "memory_ids": [
            str(item.get("memory_id"))
            for item in selected_memory_context
            if item.get("memory_id")
        ],
        "model_route_sha256": model_route["route_sha256"],
        "model_route_selection_reason": model_route.get("selection_reason"),
        "model_route_fallback_reason": model_route.get("fallback_reason"),
        "model_attempts": failover["attempts"],
        "model_failed_over": failover["failed_over"],
        "model_failover_streaming_suppressed": streaming_suppressed,
        "retrieval_strategy": retrieved[0].get("retrieval_strategy", "unknown") if retrieved else "none",
        "retrieved_chunk_ids": retrieved_ids,
        "cited_chunk_ids": cited_ids,
        "cited_chunks": cited_chunks,
        "source_ids": source_ids,
        "answer": model["answer"],
        "answer_streamed": bool(model.get("streamed")),
        "model_usage": model.get("model_usage"),
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
    if answer_override is not None:
        answer_trace["prompt_template_id"] = "agent_tools_v1"
        answer_trace["agent"] = answer_override.get("agent") or {}
    if ungrounded:
        answer_trace["grounding"] = NO_SOURCES_USED
        answer_trace["retrieval_status"] = retrieval["status"]
    answer_trace = validate_citations(answer_trace)
    answer_trace["export_paths"] = export_answer_trace(answer_trace, cfg)
    if persist:
        upsert_payload("runs", "run_id", answer_trace, cfg.sqlite_path)
    return answer_trace


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def agent_context(
    query: str,
    *,
    top_k: int = 5,
    retrieval_strategy: str = "hybrid",
    conversation_scope_id: str | None = None,
    cfg: Settings = settings,
) -> dict[str, Any]:
    """Sources and memory an agent turn is shown: the same selection answer_question makes."""
    cfg.ensure_dirs()
    ensure_db(cfg.sqlite_path)
    retrieval = retrieve(query.strip(), get_chunks(cfg), top_k=top_k, strategy=retrieval_strategy, cfg=cfg)
    if retrieval["status"].startswith("ERROR_") and retrieval["status"] not in NO_EVIDENCE_STATUSES:
        return {"status": retrieval["status"], "retrieved_chunks": [], "memory_context": []}
    retrieved = [] if retrieval["status"] in NO_EVIDENCE_STATUSES else retrieval["retrieved_chunks"]
    try:
        memory = select_memory_context(cfg, items=None, conversation_scope_id=conversation_scope_id)["selected"]
    except MemorySelectionError as exc:
        return {"status": exc.status, "retrieved_chunks": [], "memory_context": []}
    return {"status": "AGENT_CONTEXT_OK", "retrieved_chunks": retrieved, "memory_context": memory}


_REASON_TEXT = {
    "LOCAL_MODEL_SERVER_UNREACHABLE": "no model server is answering at {base_url}. Start Ollama, LM Studio or llama.cpp, or change the server URL",
    "LOCAL_MODEL_URL_NOT_LOOPBACK": "the local server URL {base_url} is not on this machine. Use the remote server option for other machines",
    "LOCAL_SERVER_LISTS_NO_MODELS": "the server at {base_url} has no model installed. Pull or load one first",
    "REMOTE_MODEL_URL_NOT_SET": "no remote server URL is set",
    "REMOTE_MODEL_URL_INVALID": "the remote server URL is not a valid http(s) address",
    "REMOTE_MODEL_SERVER_UNREACHABLE": "no model server is answering at {base_url}",
    "REMOTE_MODEL_SERVER_REJECTED_KEY": "the server at {base_url} rejected the API key",
    "REMOTE_SERVER_LISTS_NO_MODELS": "the server at {base_url} lists no models. Enter the model name",
    "LIVE_PROVIDER_NOT_EXPLICITLY_ALLOWED": "sending content to a remote model has not been allowed for this chat",
    "LIVE_MODE_DISABLED": "live mode is off (set MUCARO_AI_LIVE_MODE=1)",
    "OFFLINE_REQUIRED": "this chat is restricted to offline models",
    "SYSTEM_OFFLINE": "the network is unavailable",
}


def describe_blocked_route(model_route: dict[str, Any], preferred_provider: str | None, cfg: Settings = settings) -> str:
    """Say in words why no model could run, from the route's own candidate reasons."""
    candidates = model_route.get("candidates") or []
    row = next((item for item in candidates if item.get("provider") == preferred_provider), None)
    if row is None:
        return str(model_route.get("selection_reason") or "No eligible model provider.")
    try:
        # Served from the readiness probe cache; the explanation is best-effort, the status code is authoritative.
        base_url = str((model_provider_status(row["provider"], cfg).get("local_server") or {}).get("base_url") or "")
    except Exception:
        base_url = ""
    where = base_url or "the configured URL"
    parts: list[str] = []
    for code in [*row.get("reasons", []), *row.get("warnings", [])]:
        code = str(code)
        if code.startswith("PROVIDER_STATUS:") or code.endswith("_MODEL_CLEARTEXT_HTTP") or "_MODEL_AUTO_SELECTED:" in code:
            continue
        name, _, value = code.partition(":")
        if name.endswith("_MODEL_NOT_INSTALLED"):
            text = f"the model {value!r} is not on the server at {where}"
        elif name == "API_KEY_MISSING":
            text = f"the API key {value} is not set"
        elif name == "OPTIONAL_DEPENDENCY_MISSING":
            text = f"the Python package {value} is not installed"
        else:
            text = _REASON_TEXT.get(name, code).format(base_url=where)
        if text not in parts:
            parts.append(text)
    reason = "; ".join(parts) or str(model_route.get("selection_reason") or "it is not ready")
    return f"The model provider {row['provider']} cannot run: {reason}."
