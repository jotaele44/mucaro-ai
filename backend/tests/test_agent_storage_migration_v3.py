"""Fail-closed SQLite 2-to-3 migration and restart verification.

Fixtures are isolated SQLite files. No user database or canonical ledger is edited.
"""
from __future__ import annotations

import sqlite3

import pytest

from backend.app import db
from backend.app.agent_store import append_messages, load_history
from backend.tests.test_api import make_settings


def _make_v2_fixture(path):
    """Reconstruct an established v2 schema using its shared table contract."""
    db.init_db(path)
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE agent_messages")
        conn.execute("DROP TABLE agent_conversations")
        conn.execute("UPDATE schema_meta SET value='2' WHERE key='schema_version'")
        conn.execute(
            "INSERT INTO conversations (conversation_id, payload_json) VALUES (?, ?)",
            ("legacy-1", '{"retained":true}'),
        )
        conn.execute(
            "INSERT INTO run_events (run_id,seq,event_type,payload_json) VALUES (?,?,?,?)",
            ("run-1", 1, "progress", '{"event":"preserved"}'),
        )


def test_v2_upgrade_is_idempotent_and_preserves_existing_rows(tmp_path):
    cfg = make_settings(tmp_path)
    _make_v2_fixture(cfg.sqlite_path)
    with sqlite3.connect(cfg.sqlite_path) as conn:
        original_conversation = conn.execute("SELECT payload_json FROM conversations").fetchone()[0]
        original_event = conn.execute("SELECT payload_json FROM run_events").fetchone()[0]

    db.init_db(cfg.sqlite_path)
    db.init_db(cfg.sqlite_path)
    assert db.schema_version(cfg.sqlite_path) == 3
    with sqlite3.connect(cfg.sqlite_path) as conn:
        assert conn.execute("SELECT payload_json FROM conversations").fetchone()[0] == original_conversation
        assert conn.execute("SELECT payload_json FROM run_events").fetchone()[0] == original_event
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='agent_messages'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='agent_conversations'").fetchone()[0] == 1


def test_restart_replays_unchanged_content_and_sequence(tmp_path):
    cfg = make_settings(tmp_path)
    _make_v2_fixture(cfg.sqlite_path)
    db.init_db(cfg.sqlite_path)
    payload = [{"type": "text", "text": "No rewrite"}]
    append_messages("legacy-1", [{"role": "user", "content": payload}], cfg)
    before = load_history("legacy-1", cfg)
    db.init_db(cfg.sqlite_path)  # emulate a process startup against persisted DB
    after = load_history("legacy-1", cfg)
    assert before == after == [{"role": "user", "content": payload}]


def test_migration_failure_rolls_back_new_tables_and_version(tmp_path, monkeypatch):
    cfg = make_settings(tmp_path)
    _make_v2_fixture(cfg.sqlite_path)
    original = db._SCHEMA_SQL
    monkeypatch.setattr(db, "_SCHEMA_SQL", original + "\nCREATE TABLE malformed (\n")
    with pytest.raises(sqlite3.OperationalError):
        db.init_db(cfg.sqlite_path)
    with sqlite3.connect(cfg.sqlite_path) as conn:
        assert conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0] == "2"
        for table in ("agent_messages", "agent_conversations"):
            assert conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM conversations WHERE conversation_id='legacy-1'").fetchone()[0] == 1


def test_newer_schema_refuses_and_does_not_modify_bytes(tmp_path):
    cfg = make_settings(tmp_path)
    db.init_db(cfg.sqlite_path)
    with sqlite3.connect(cfg.sqlite_path) as conn:
        conn.execute("UPDATE schema_meta SET value='900' WHERE key='schema_version'")
        before = list(conn.execute("SELECT name, sql FROM sqlite_master ORDER BY name"))
    with pytest.raises(db.SchemaVersionError):
        db.init_db(cfg.sqlite_path)
    with sqlite3.connect(cfg.sqlite_path) as conn:
        after = list(conn.execute("SELECT name, sql FROM sqlite_master ORDER BY name"))
        assert conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0] == "900"
    assert before == after
