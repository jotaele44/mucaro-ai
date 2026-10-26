from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from .config import parse_bool, settings

# Bump when tables change; init_db only ever moves the recorded version forward.
SCHEMA_VERSION = 3


def get_connection(sqlite_path: Path | None = None) -> sqlite3.Connection:
    path = sqlite_path or settings.sqlite_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


class SchemaVersionError(RuntimeError):
    """The database was written by a newer Mucaro; refuse rather than risk corrupting it (TGT-029)."""

    def __init__(self, found: int, supported: int) -> None:
        super().__init__(f"database schema {found} is newer than this app supports ({supported})")
        self.status = "ERROR_DATABASE_SCHEMA_TOO_NEW"
        self.found = found
        self.supported = supported


_SCHEMA_SQL = """
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
            CREATE TABLE IF NOT EXISTS work_runs (
                run_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                queue_seq INTEGER NOT NULL,
                state TEXT NOT NULL,
                idempotency_key TEXT,
                version INTEGER NOT NULL DEFAULT 1,
                payload_json TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS work_runs_idempotency
                ON work_runs(conversation_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
            CREATE INDEX IF NOT EXISTS work_runs_conversation ON work_runs(conversation_id, queue_seq);
            CREATE TABLE IF NOT EXISTS run_events (
                run_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (run_id, seq)
            );
            CREATE TABLE IF NOT EXISTS execution_entry_artifacts (
                artifact_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                mode TEXT NOT NULL,
                state TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                source_commit TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (artifact_id, revision)
            );
            CREATE INDEX IF NOT EXISTS execution_entry_artifacts_conversation
                ON execution_entry_artifacts(conversation_id, rowid);
            CREATE TABLE IF NOT EXISTS execution_entry_decisions (
                decision_id TEXT PRIMARY KEY,
                artifact_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                request_sha256 TEXT NOT NULL,
                state TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(artifact_id, revision) REFERENCES execution_entry_artifacts(artifact_id, revision)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS execution_entry_active_claim
                ON execution_entry_decisions(artifact_id, revision)
                WHERE state IN ('CLAIMED','DISPATCHED');
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """


def init_db(sqlite_path: Path | None = None) -> None:
    """Create or upgrade the schema atomically; a newer schema fails safe instead of being touched."""
    conn = get_connection(sqlite_path)
    try:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            has_meta = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_meta'").fetchone()
            if has_meta:
                row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
                found = int(row["value"]) if row else 0
                if found > SCHEMA_VERSION:
                    raise SchemaVersionError(found, SCHEMA_VERSION)
            for statement in [part.strip() for part in _SCHEMA_SQL.split(";") if part.strip()]:
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value WHERE CAST(value AS INTEGER) < CAST(excluded.value AS INTEGER)",
                (str(SCHEMA_VERSION),),
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        # Only after the version check, so a newer database is refused without being touched.
        # WAL keeps durability (synchronous stays FULL) with one fsync per commit, and readers
        # stop blocking the writer. It persists in the file; MUCARO_AI_SQLITE_WAL=0 opts out.
        if parse_bool(os.getenv("MUCARO_AI_SQLITE_WAL") or None, True):
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                pass  # Best effort: a busy or unsupported file keeps its journal mode.
    finally:
        conn.close()


_INITIALIZED_PATHS: set[str] = set()


def ensure_db(sqlite_path: Path | None = None) -> None:
    """init_db once per database path per process (init_db itself is idempotent)."""
    path = sqlite_path or settings.sqlite_path
    key = str(path.resolve()) if path.exists() else str(path)
    if key in _INITIALIZED_PATHS and path.exists():
        return
    init_db(path)
    _INITIALIZED_PATHS.add(str(path.resolve()))


PAYLOAD_TABLES = frozenset({"sources", "chunks", "runs", "execution_gates", "conversations", "agent_checkpoints", "interactions"})
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")


def _checked_identifiers(table: str, key_field: str | None = None) -> None:
    """Table and column names cannot be bound as SQL parameters, so they are allow-listed/validated instead."""
    if table not in PAYLOAD_TABLES:
        raise ValueError(f"Invalid table {table}")
    if key_field is not None and not _IDENTIFIER.match(key_field):
        raise ValueError(f"Invalid key field {key_field!r}")


def upsert_payload(table: str, key_field: str, payload: dict[str, Any], sqlite_path: Path | None = None) -> None:
    _checked_identifiers(table, key_field)
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
    _checked_identifiers(table)
    with get_connection(sqlite_path) as conn:
        rows = conn.execute(f"SELECT payload_json FROM {table} ORDER BY rowid ASC").fetchall()
    return [json.loads(row["payload_json"]) for row in rows]


def delete_payload(table: str, key_field: str, key: str, sqlite_path: Path | None = None) -> int:
    _checked_identifiers(table, key_field)
    with get_connection(sqlite_path) as conn:
        cursor = conn.execute(f"DELETE FROM {table} WHERE {key_field} = ?", (key,))
        return int(cursor.rowcount)


def delete_chunks_for_source(source_id: str, sqlite_path: Path | None = None) -> int:
    with get_connection(sqlite_path) as conn:
        cursor = conn.execute("DELETE FROM chunks WHERE source_id = ?", (source_id,))
        return int(cursor.rowcount)


def schema_version(sqlite_path: Path | None = None) -> int:
    with get_connection(sqlite_path) as conn:
        row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    return int(row["value"]) if row else 0
