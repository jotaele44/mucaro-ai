"""Append-only storage for each conversation's Claude message history.

Rows hold content blocks exactly as the API returned them, serialized once and
replayed byte-for-byte on every later request, so cached prefixes and thinking
blocks stay valid. Rows are never updated; deleting a conversation deletes its
history.
"""

from __future__ import annotations

import json
from typing import Any

from .config import Settings, settings
from .db import ensure_db, get_connection
from .utils import utc_now


def dumps_content(content: Any) -> str:
    return json.dumps(content, ensure_ascii=False, separators=(",", ":"))


def load_history(conversation_id: str, cfg: Settings = settings) -> list[dict[str, Any]]:
    """The conversation as API messages: ``[{"role", "content"}]`` in order."""
    ensure_db(cfg.sqlite_path)
    with get_connection(cfg.sqlite_path) as conn:
        rows = conn.execute(
            "SELECT role, content_json FROM agent_messages WHERE conversation_id = ? ORDER BY seq ASC",
            (conversation_id,),
        ).fetchall()
    return [{"role": row["role"], "content": json.loads(row["content_json"])} for row in rows]


def load_history_rows(conversation_id: str, cfg: Settings = settings) -> list[dict[str, Any]]:
    ensure_db(cfg.sqlite_path)
    with get_connection(cfg.sqlite_path) as conn:
        rows = conn.execute(
            "SELECT seq, run_id, turn_id, role, content_json, created_at FROM agent_messages WHERE conversation_id = ? ORDER BY seq ASC",
            (conversation_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def append_messages(conversation_id: str, rows: list[dict[str, Any]], cfg: Settings = settings) -> int:
    """Append rows of ``{"role", "content_json" | "content", "run_id"?, "turn_id"?}`` in one transaction."""
    if not rows:
        return 0
    ensure_db(cfg.sqlite_path)
    conn = get_connection(cfg.sqlite_path)
    try:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            last = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS seq FROM agent_messages WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()["seq"]
            now = utc_now()
            for offset, row in enumerate(rows, start=1):
                if row.get("role") not in {"user", "assistant"}:
                    raise ValueError(f"agent message role must be user or assistant: {row.get('role')!r}")
                content_json = row.get("content_json") or dumps_content(row["content"])
                conn.execute(
                    "INSERT INTO agent_messages (conversation_id, seq, run_id, turn_id, role, content_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (conversation_id, int(last) + offset, row.get("run_id"), row.get("turn_id"), row["role"], content_json, now),
                )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return len(rows)


def represented_ids(conversation_id: str, cfg: Settings = settings) -> tuple[set[str], set[str]]:
    """Run ids and seeded turn ids that already have messages in the history."""
    ensure_db(cfg.sqlite_path)
    with get_connection(cfg.sqlite_path) as conn:
        rows = conn.execute("SELECT DISTINCT run_id, turn_id FROM agent_messages WHERE conversation_id = ?", (conversation_id,)).fetchall()
    return {row["run_id"] for row in rows if row["run_id"]}, {row["turn_id"] for row in rows if row["turn_id"]}


def get_agent_conversation(conversation_id: str, cfg: Settings = settings) -> dict[str, Any] | None:
    ensure_db(cfg.sqlite_path)
    with get_connection(cfg.sqlite_path) as conn:
        row = conn.execute("SELECT payload_json FROM agent_conversations WHERE conversation_id = ?", (conversation_id,)).fetchone()
    return json.loads(row["payload_json"]) if row else None


def save_agent_conversation(conversation_id: str, payload: dict[str, Any], cfg: Settings = settings) -> None:
    ensure_db(cfg.sqlite_path)
    with get_connection(cfg.sqlite_path) as conn:
        conn.execute(
            "INSERT INTO agent_conversations (conversation_id, payload_json) VALUES (?, ?) "
            "ON CONFLICT(conversation_id) DO UPDATE SET payload_json = excluded.payload_json",
            (conversation_id, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
        )


def delete_agent_conversation(conversation_id: str, cfg: Settings = settings) -> None:
    ensure_db(cfg.sqlite_path)
    with get_connection(cfg.sqlite_path) as conn:
        conn.execute("DELETE FROM agent_messages WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM agent_conversations WHERE conversation_id = ?", (conversation_id,))
