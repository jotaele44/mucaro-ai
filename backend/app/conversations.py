from __future__ import annotations

import threading
from typing import Any

from .config import Settings, settings
from .context_state import build_context_state, build_model_context
from .db import delete_payload, init_db, list_payloads, upsert_payload
from .utils import stable_id, utc_now

DEFAULT_TITLE = "New chat"
# Serializes read-modify-write of a conversation row within this process.
_CONVERSATION_LOCK = threading.RLock()
MAX_CONTEXT_TURNS = 8
MAX_CONTEXT_CHARS = 2400


class ConversationMessageEditError(RuntimeError):
    def __init__(self, status: str, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


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
        "message_edits": {},
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


def edit_conversation_message(
    conversation_id: str,
    message_key: str,
    content: str,
    expected_revision: int,
    cfg: Settings = settings,
) -> dict[str, Any]:
    """Store a display-only message revision without mutating the canonical turn or run."""
    if not content.strip():
        raise ConversationMessageEditError("ERROR_MESSAGE_EDIT_EMPTY", "Edited message content is required.")
    with _CONVERSATION_LOCK:
        conversation = get_conversation(conversation_id, cfg)
        if conversation is None:
            raise ConversationMessageEditError("ERROR_CONVERSATION_NOT_FOUND", "Conversation not found.")
        raw_edits = conversation.get("message_edits") or {}
        if not isinstance(raw_edits, dict):
            raise ConversationMessageEditError(
                "ERROR_MESSAGE_EDITS_INVALID",
                "Stored message edits are not a valid mapping.",
            )
        edits = dict(raw_edits)
        previous = edits.get(message_key)
        current_revision = int(previous.get("revision", 0)) if isinstance(previous, dict) else 0
        if expected_revision != current_revision:
            raise ConversationMessageEditError(
                "ERROR_MESSAGE_EDIT_REVISION_CONFLICT",
                f"Expected revision {expected_revision}, current revision is {current_revision}.",
            )
        now = utc_now()
        edit = {
            "message_key": message_key,
            "content": content,
            "revision": current_revision + 1,
            "edited_at": now,
            "scope": "DISPLAY_OVERRIDE",
            "original_preserved": True,
        }
        edits[message_key] = edit
        conversation["message_edits"] = edits
        conversation["updated_at"] = now
        upsert_payload("conversations", "conversation_id", conversation, cfg.sqlite_path)
        return edit


def build_conversation_context(conversation: dict[str, Any]) -> list[dict[str, Any]]:
    """Build bounded model context without mutating or deleting canonical turns."""
    state = build_context_state(
        conversation,
        recent_turn_limit=MAX_CONTEXT_TURNS,
        recent_char_limit=MAX_CONTEXT_CHARS,
    )
    return build_model_context(state)


def append_turns(
    conversation_id: str,
    turns: list[dict[str, Any]],
    cfg: Settings = settings,
    *,
    title_hint: str | None = None,
) -> dict[str, Any]:
    """Append pre-built turns; turn IDs already present are not duplicated (idempotent replay)."""
    with _CONVERSATION_LOCK:
        conversation = get_conversation(conversation_id, cfg)
        if conversation is None:
            raise KeyError(conversation_id)
        existing = {turn.get("turn_id") for turn in conversation.get("turns", [])}
        new_turns = [turn for turn in turns if turn.get("turn_id") not in existing]
        conversation["turns"] = list(conversation.get("turns", [])) + new_turns
        if conversation.get("title") == DEFAULT_TITLE and title_hint:
            conversation["title"] = _title_from_query(title_hint)
        conversation["updated_at"] = utc_now()
        upsert_payload("conversations", "conversation_id", conversation, cfg.sqlite_path)
        return conversation


def append_answer_turn(
    conversation_id: str,
    query: str,
    answer_trace: dict[str, Any],
    cfg: Settings = settings,
) -> dict[str, Any]:
    with _CONVERSATION_LOCK:
        return _append_answer_turn(conversation_id, query, answer_trace, cfg)


def _append_answer_turn(
    conversation_id: str,
    query: str,
    answer_trace: dict[str, Any],
    cfg: Settings,
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
    if conversation.get("title") == DEFAULT_TITLE:
        conversation["title"] = _title_from_query(query)
    conversation["updated_at"] = now
    upsert_payload("conversations", "conversation_id", conversation, cfg.sqlite_path)
    return conversation
