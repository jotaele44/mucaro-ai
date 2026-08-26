from __future__ import annotations

from typing import Any

from .config import Settings, settings
from .db import init_db, list_payloads, upsert_payload
from .utils import stable_id, utc_now

DEFAULT_TITLE = "New chat"


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
    if conversation.get("title") == DEFAULT_TITLE:
        conversation["title"] = _title_from_query(query)
    conversation["updated_at"] = now
    upsert_payload("conversations", "conversation_id", conversation, cfg.sqlite_path)
    return conversation
