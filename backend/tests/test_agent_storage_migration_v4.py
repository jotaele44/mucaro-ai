"""Forward-only SQLite upgrade to schema 4 (agent message history), from real older databases.

The v2 and v3 fixtures are built from the DDL those releases actually shipped (frozen copies under
``fixtures/db_schema_v{2,3}.sql``), not by dropping tables from a current database. The storage
foundation was first written as a 2-to-3 step; main had already used version 3 for the
execution-entry tables, so it lands as 3-to-4 and is also verified straight from 2.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from backend.app import db
from backend.app.agent_store import append_messages, load_history
from backend.app.conversations import get_conversation, list_conversations
from backend.tests.test_api import make_settings

FIXTURES = Path(__file__).parent / "fixtures"
AGENT_TABLES = ("agent_messages", "agent_conversations")
CONVERSATION = {
    "conversation_id": "conv_legacy",
    "title": "Kept",
    "created_at": "2026-10-01T00:00:00+00:00",
    "updated_at": "2026-10-01T00:00:00+00:00",
    "turns": [
        {"turn_id": "turn_1", "role": "user", "content": "What was decided?", "created_at": "2026-10-01T00:00:00+00:00"},
        {"turn_id": "turn_2", "role": "assistant", "content": "Ship on Friday.", "created_at": "2026-10-01T00:00:01+00:00"},
    ],
}


def _old_database(path: Path, version: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript((FIXTURES / f"db_schema_v{version}.sql").read_text(encoding="utf-8"))
        conn.execute("INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?)", (str(version),))
        conn.execute("INSERT INTO conversations VALUES (?, ?)", (CONVERSATION["conversation_id"], json.dumps(CONVERSATION, sort_keys=True)))
        conn.execute("INSERT INTO run_events (run_id, seq, event_type, payload_json) VALUES ('run-1', 1, 'final', '{\"event\":\"preserved\"}')")
        conn.execute(
            "INSERT INTO work_runs (run_id, conversation_id, queue_seq, state, payload_json) VALUES ('run-1', 'conv_legacy', 1, 'COMPLETED', '{}')"
        )
    return path


def _snapshot(path: Path) -> dict[str, list[tuple]]:
    with sqlite3.connect(path) as conn:
        return {table: conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() for table in ("conversations", "run_events", "work_runs")}


def _tables(path: Path) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


@pytest.mark.parametrize("version", [2, 3])
def test_old_database_fixture_is_really_that_version(tmp_path, version):
    path = _old_database(tmp_path / "old.sqlite3", version)
    assert db.schema_version(path) == version
    assert not set(AGENT_TABLES) & _tables(path)
    assert ("execution_entry_artifacts" in _tables(path)) == (version == 3)


@pytest.mark.parametrize("version", [2, 3])
def test_upgrade_is_idempotent_and_keeps_every_existing_row(tmp_path, version):
    cfg = make_settings(tmp_path)
    _old_database(cfg.sqlite_path, version)
    before = _snapshot(cfg.sqlite_path)

    db.init_db(cfg.sqlite_path)
    first = _snapshot(cfg.sqlite_path)
    db.init_db(cfg.sqlite_path)

    assert db.schema_version(cfg.sqlite_path) == db.SCHEMA_VERSION == 4
    assert before == first == _snapshot(cfg.sqlite_path)
    assert set(AGENT_TABLES) | {"execution_entry_artifacts", "execution_entry_decisions"} <= _tables(cfg.sqlite_path)
    # The conversation is still readable through the app's own API, turns intact.
    assert [row["conversation_id"] for row in list_conversations(cfg)] == ["conv_legacy"]
    assert [turn["content"] for turn in get_conversation("conv_legacy", cfg)["turns"]] == ["What was decided?", "Ship on Friday."]
    assert load_history("conv_legacy", cfg) == []  # nothing is invented for old conversations


def test_history_written_after_the_upgrade_survives_a_restart(tmp_path):
    cfg = make_settings(tmp_path)
    _old_database(cfg.sqlite_path, 2)
    db.init_db(cfg.sqlite_path)
    payload = [{"type": "text", "text": "No rewrite"}]
    append_messages("conv_legacy", [{"role": "user", "content": payload}], cfg)
    db.init_db(cfg.sqlite_path)  # the next process start
    assert load_history("conv_legacy", cfg) == [{"role": "user", "content": payload}]


@pytest.mark.parametrize("version", [2, 3])
def test_failed_upgrade_rolls_back_tables_and_version(tmp_path, monkeypatch, version):
    cfg = make_settings(tmp_path)
    _old_database(cfg.sqlite_path, version)
    before = _snapshot(cfg.sqlite_path)
    monkeypatch.setattr(db, "_SCHEMA_SQL", db._SCHEMA_SQL + ";\nCREATE TABLE malformed (")
    with pytest.raises(sqlite3.OperationalError):
        db.init_db(cfg.sqlite_path)
    assert db.schema_version(cfg.sqlite_path) == version
    assert not set(AGENT_TABLES) & _tables(cfg.sqlite_path)
    assert _snapshot(cfg.sqlite_path) == before


def test_version_never_moves_backwards(tmp_path):
    cfg = make_settings(tmp_path)
    db.init_db(cfg.sqlite_path)
    with sqlite3.connect(cfg.sqlite_path) as conn:
        conn.execute("UPDATE schema_meta SET value = '900' WHERE key = 'schema_version'")
        before = list(conn.execute("SELECT name, sql FROM sqlite_master ORDER BY name"))
    with pytest.raises(db.SchemaVersionError):
        db.init_db(cfg.sqlite_path)
    with sqlite3.connect(cfg.sqlite_path) as conn:
        assert list(conn.execute("SELECT name, sql FROM sqlite_master ORDER BY name")) == before
    assert db.schema_version(cfg.sqlite_path) == 900
