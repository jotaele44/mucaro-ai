from __future__ import annotations

from typing import Any

from .config import Settings, settings
from .context_state import ContextStateError, build_context_state, build_model_context, validate_context_state, validate_context_state_schema
from .db import delete_payload, init_db, list_payloads, upsert_payload
from .utils import stable_id, utc_now

DEFAULT_TITLE = "New chat"
MAX_CONTEXT_TURNS = 8
MAX_CONTEXT_CHARS = 2400


def _title_from_query(query: str) -> str:
    compact = " ".join(query.strip().split())
    return compact[:64] or DEFAULT_TITLE


def create_conversation(title: str | None = None, cfg: Settings = settings) -> dict[str, Any]:
    cfg.ensure_dirs()
    init_db(cfg.sqlite_path)
    now = utc_now()
    resolved_title = (title or DEFAULT_TITLE).strip() or DEFAULT_TITLE
    conversation = {
        "conversation_id": stable_id("conv", resolved_title, now, length=12),
        "title": resolved_title,
        "created_at": now,
        "updated_at": now,
        "turns": [],
    }
    upsert_payload("conversations", "conversation_id", conversation, cfg.sqlite_path)
    return conversation


def list_conversations(cfg: Settings = settings) -> list[dict[str, Any]]:
    init_db(cfg.sqlite_path)
    conversations = list_payloads("conversations", cfg.sqlite_path)
    summaries = []
    for conversation in conversations:
        turns = conversation.get("turns", [])
        summaries.append(
            {
                "conversation_id": conversation.get("conversation_id"),
                "title": conversation.get("title", DEFAULT_TITLE),
                "created_at": conversation.get("created_at"),
                "updated_at": conversation.get("updated_at"),
                "turn_count": len(turns),
                "last_status": turns[-1].get("status") if turns else "EMPTY",
            }
        )
    return sorted(summaries, key=lambda item: item.get("updated_at") or "", reverse=True)


def get_conversation(conversation_id: str, cfg: Settings = settings) -> dict[str, Any] | None:
    init_db(cfg.sqlite_path)
    for conversation in list_payloads("conversations", cfg.sqlite_path):
        if conversation.get("conversation_id") == conversation_id:
            return conversation
    return None


def rename_conversation(conversation_id: str, title: str, cfg: Settings = settings) -> dict[str, Any]:
    conversation = get_conversation(conversation_id, cfg)
    if conversation is None:
        raise KeyError(conversation_id)
    resolved_title = " ".join(title.strip().split())
    if not resolved_title:
        raise ValueError("ERROR_CONVERSATION_TITLE_REQUIRED")
    conversation["title"] = resolved_title[:120]
    conversation["updated_at"] = utc_now()
    upsert_payload("conversations", "conversation_id", conversation, cfg.sqlite_path)
    return conversation


def delete_conversation(conversation_id: str, cfg: Settings = settings) -> dict[str, Any]:
    init_db(cfg.sqlite_path)
    removed = delete_payload("conversations", "conversation_id", conversation_id, cfg.sqlite_path)
    return {
        "status": "CONVERSATION_DELETE_OK" if removed else "ERROR_CONVERSATION_NOT_FOUND",
        "conversation_id": conversation_id,
        "removed": removed,
    }


def _context_state_for(conversation: dict[str, Any]) -> dict[str, Any]:
    return build_context_state(
        conversation,
        recent_turn_limit=MAX_CONTEXT_TURNS,
        recent_char_limit=MAX_CONTEXT_CHARS,
    )


def build_conversation_context(conversation: dict[str, Any]) -> list[dict[str, Any]]:
    """Build bounded model context without mutating or deleting canonical turns.

    A previously persisted context state is never trusted as history: it is verified against the
    canonical turns (a rewritten turn fails closed with ContextStateError) and the model context is
    always rebuilt from those turns. A state that is merely behind (newer turns) is not an error.
    """
    stored = conversation.get("context_state")
    if isinstance(stored, dict):
        try:
            validate_context_state(conversation, stored)
        except ContextStateError as exc:
            if exc.status != "ERROR_CONTEXT_STALE":
                raise
    return build_model_context(_context_state_for(conversation))


def append_answer_turn(
    conversation_id: str,
    query: str,
    answer_trace: dict[str, Any],
    cfg: Settings = settings,
) -> dict[str, Any]:
    conversation = get_conversation(conversation_id, cfg)
    if conversation is None:
        raise KeyError(conversation_id)
    now = utc_now()
    turns = list(conversation.get("turns", []))
    turns.append(
        {
            "turn_id": stable_id("turn", conversation_id, "user", query, now, length=12),
            "role": "user",
            "content": query,
            "created_at": now,
            "status": "USER_MESSAGE",
        }
    )
    assistant_content = answer_trace.get("answer") or answer_trace.get("detail") or answer_trace.get("status", "")
    turns.append(
        {
            "turn_id": stable_id("turn", conversation_id, "assistant", str(answer_trace.get("run_id", "")), now, length=12),
            "role": "assistant",
            "content": assistant_content,
            "created_at": now,
            "status": answer_trace.get("citation_status") or answer_trace.get("status") or "DONE",
            "run_id": answer_trace.get("run_id"),
            "retrieval_strategy": answer_trace.get("retrieval_strategy"),
            "cited_chunk_ids": answer_trace.get("cited_chunk_ids", []),
            "source_ids": answer_trace.get("source_ids", []),
            "validation_flags": answer_trace.get("validation_flags", []),
            "answer_trace": answer_trace,
        }
    )
    conversation["turns"] = turns
    # Persist the derived state so restarts can verify (not trust) it against the canonical turns.
    context_state = _context_state_for(conversation)
    validate_context_state_schema(context_state)
    conversation["context_state"] = context_state
    if conversation.get("title") == DEFAULT_TITLE:
        conversation["title"] = _title_from_query(query)
    conversation["updated_at"] = now
    upsert_payload("conversations", "conversation_id", conversation, cfg.sqlite_path)
    return conversation
