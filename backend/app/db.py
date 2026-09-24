from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .config import settings


def get_connection(sqlite_path: Path | None = None) -> sqlite3.Connection:
    path = sqlite_path or settings.sqlite_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(sqlite_path: Path | None = None) -> None:
    with get_connection(sqlite_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sources (
                source_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(source_id) REFERENCES sources(source_id)
            );
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS execution_gates (
                task_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversations (
                conversation_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_checkpoints (
                checkpoint_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS interactions (
                interaction_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            );
            """
        )


def upsert_payload(table: str, key_field: str, payload: dict[str, Any], sqlite_path: Path | None = None) -> None:
    allowed = {"sources", "chunks", "runs", "execution_gates", "conversations", "agent_checkpoints", "interactions"}
    if table not in allowed:
        raise ValueError(f"Invalid table {table}")
    key = payload[key_field]
    with get_connection(sqlite_path) as conn:
        if table == "chunks":
            conn.execute(
                "INSERT OR REPLACE INTO chunks (chunk_id, source_id, payload_json) VALUES (?, ?, ?)",
                (key, payload.get("source_id", ""), json.dumps(payload, ensure_ascii=False, sort_keys=True)),
            )
        else:
            conn.execute(
                f"INSERT OR REPLACE INTO {table} ({key_field}, payload_json) VALUES (?, ?)",
                (key, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
            )


def list_payloads(table: str, sqlite_path: Path | None = None) -> list[dict[str, Any]]:
    allowed = {"sources", "chunks", "runs", "execution_gates", "conversations", "agent_checkpoints", "interactions"}
    if table not in allowed:
        raise ValueError(f"Invalid table {table}")
    with get_connection(sqlite_path) as conn:
        rows = conn.execute(f"SELECT payload_json FROM {table} ORDER BY rowid ASC").fetchall()
    return [json.loads(row["payload_json"]) for row in rows]


def delete_payload(table: str, key_field: str, key: str, sqlite_path: Path | None = None) -> int:
    allowed = {"sources", "chunks", "runs", "execution_gates", "conversations", "agent_checkpoints", "interactions"}
    if table not in allowed:
        raise ValueError(f"Invalid table {table}")
    with get_connection(sqlite_path) as conn:
        cursor = conn.execute(f"DELETE FROM {table} WHERE {key_field} = ?", (key,))
        return int(cursor.rowcount)


def delete_chunks_for_source(source_id: str, sqlite_path: Path | None = None) -> int:
    with get_connection(sqlite_path) as conn:
        cursor = conn.execute("DELETE FROM chunks WHERE source_id = ?", (source_id,))
        return int(cursor.rowcount)
