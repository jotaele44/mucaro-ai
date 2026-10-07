from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

from .config import Settings, settings
from .context_state import build_context_state, build_model_context
from .db import delete_payload, get_connection, init_db, list_payloads, upsert_payload
from .utils import stable_id, utc_now

DEFAULT_TITLE = "New chat"
MAX_CONTEXT_TURNS = 8
MAX_CONTEXT_CHARS = 2400


class ConversationMessageEditError(RuntimeError):
    def __init__(self, status: str, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class ConversationRevisionError(RuntimeError):
    def __init__(self, current_revision: int):
        super().__init__("Conversation changed. Reload it before saving.")
        self.current_revision = current_revision


@contextmanager
def _conversation_write(conversation_id: str, cfg: Settings):
    """Serialize every read-modify-write across processes, including message writers."""
    init_db(cfg.sqlite_path)
    conn = get_connection(cfg.sqlite_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT payload_json FROM conversations WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()
        if row is None:
            raise KeyError(conversation_id)
        conversation = json.loads(row["payload_json"])
        before = json.dumps(conversation, ensure_ascii=False, sort_keys=True)
        yield conversation
        if json.dumps(conversation, ensure_ascii=False, sort_keys=True) != before:
            conversation["revision"] = conversation.get("revision", 0) + 1
            conversation["updated_at"] = utc_now()
            conn.execute(
                "UPDATE conversations SET payload_json = ? WHERE conversation_id = ?",
                (json.dumps(conversation, ensure_ascii=False, sort_keys=True), conversation_id),
            )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _title_from_query(query: str) -> str:
    compact = " ".join(query.strip().split())
    return compact[:64] or DEFAULT_TITLE


def create_conversation(title: str | None = None, cfg: Settings = settings) -> dict[str, Any]:
    cfg.ensure_dirs()
    init_db(cfg.sqlite_path)
    now = utc_now()
    resolved_title = (title or DEFAULT_TITLE).strip() or DEFAULT_TITLE
    conversation = {
        "conversation_id": f"conv_{uuid4().hex}",
        "title": resolved_title,
        "created_at": now,
        "updated_at": now,
        "turns": [],
        "message_edits": {},
        "pinned": False,
        "archived": False,
        "revision": 0,
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
                "pinned": conversation.get("pinned", False),
                "archived": conversation.get("archived", False),
                "revision": conversation.get("revision", 0),
            }
        )
    # Stable ID breaks timestamp ties independently of SQLite row order.
    summaries.sort(key=lambda item: item["conversation_id"])
    summaries.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    summaries.sort(key=lambda item: (item["archived"], not item["pinned"]))
    return summaries


def get_conversation(conversation_id: str, cfg: Settings = settings) -> dict[str, Any] | None:
    init_db(cfg.sqlite_path)
    conn = get_connection(cfg.sqlite_path)
    try:
        row = conn.execute(
            "SELECT payload_json FROM conversations WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()
        return json.loads(row["payload_json"]) if row else None
    finally:
        conn.close()


def rename_conversation(conversation_id: str, title: str, cfg: Settings = settings) -> dict[str, Any]:
    return update_conversation(conversation_id, {"title": title}, cfg)


def update_conversation(
    conversation_id: str,
    changes: dict[str, Any],
    cfg: Settings = settings,
    *,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    if not changes or set(changes) - {"title", "pinned", "archived"}:
        raise ValueError("ERROR_CONVERSATION_UPDATE_INVALID")
    changes = dict(changes)
    if "title" in changes:
        if not isinstance(changes["title"], str) or not changes["title"].strip():
            raise ValueError("ERROR_CONVERSATION_TITLE_REQUIRED")
        changes["title"] = " ".join(changes["title"].strip().split())[:120]
    if any(type(changes[key]) is not bool for key in {"pinned", "archived"} & changes.keys()):
        raise ValueError("ERROR_CONVERSATION_UPDATE_INVALID")
    if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
        raise ValueError("ERROR_CONVERSATION_REVISION_INVALID")
    with _conversation_write(conversation_id, cfg) as conversation:
        revision = conversation.get("revision", 0)
        if expected_revision is not None and expected_revision != revision:
            raise ConversationRevisionError(revision)
        conversation.update(changes)
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
    with _conversation_write(conversation_id, cfg) as conversation:
        raw_edits = conversation.get("message_edits") or {}
        if not isinstance(raw_edits, dict):
            raise ConversationMessageEditError(
                "ERROR_MESSAGE_EDITS_INVALID",
                "Stored message edits are not a valid mapping.",
            )
        edits = dict(raw_edits)
        previous = edits.get(message_key)
        if previous is not None and not isinstance(previous, dict):
            raise ConversationMessageEditError(
                "ERROR_MESSAGE_EDITS_INVALID",
                "Stored message edit entry is not a valid mapping.",
            )
        try:
            current_revision = int(previous.get("revision", 0)) if previous is not None else 0
        except (TypeError, ValueError) as exc:
            raise ConversationMessageEditError(
                "ERROR_MESSAGE_EDITS_INVALID",
                "Stored message edit revision is not valid.",
            ) from exc
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
    """Replay identical turns without changes; reject conflicting reuse of a turn ID."""
    with _conversation_write(conversation_id, cfg) as conversation:
        existing = {turn.get("turn_id"): turn for turn in conversation.get("turns", [])}
        new_turns = []
        for turn in turns:
            turn_id = turn.get("turn_id")
            if not isinstance(turn_id, str) or not turn_id:
                raise ValueError("ERROR_TURN_ID_REQUIRED")
            if turn_id in existing and existing[turn_id] != turn:
                raise ValueError("ERROR_TURN_ID_CONFLICT")
            if turn_id not in existing:
                new_turns.append(turn)
                existing[turn_id] = turn
        if not new_turns:
            return conversation
        conversation["turns"] = list(conversation.get("turns", [])) + new_turns
        if conversation.get("title") == DEFAULT_TITLE and title_hint:
            conversation["title"] = _title_from_query(title_hint)
        conversation["updated_at"] = utc_now()
        return conversation


def append_answer_turn(
    conversation_id: str,
    query: str,
    answer_trace: dict[str, Any],
    cfg: Settings = settings,
) -> dict[str, Any]:
    with _conversation_write(conversation_id, cfg) as conversation:
        _append_answer_turn(conversation, conversation_id, query, answer_trace)
    return conversation


def _append_answer_turn(
    conversation: dict[str, Any],
    conversation_id: str,
    query: str,
    answer_trace: dict[str, Any],
) -> dict[str, Any]:
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
    return conversation
