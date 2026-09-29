from __future__ import annotations

import json
import sqlite3

import pytest

from backend.app import db
from backend.app.db import SCHEMA_VERSION, SchemaVersionError, init_db, list_payloads, schema_version

V1_TABLES = """
CREATE TABLE sources (source_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL);
CREATE TABLE chunks (chunk_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE runs (run_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL);
CREATE TABLE execution_gates (task_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL);
CREATE TABLE conversations (conversation_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL);
"""


def _v1_database(path):
    with sqlite3.connect(path) as conn:
        conn.executescript(V1_TABLES)
        conn.execute("INSERT INTO conversations VALUES (?, ?)", ("conv_old", json.dumps({"conversation_id": "conv_old", "title": "Kept", "turns": []})))
    return path


def _tables(path):
    with sqlite3.connect(path) as conn:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_upgrade_from_v1_adds_tables_and_preserves_data(tmp_path):
    path = _v1_database(tmp_path / "old.sqlite3")
    init_db(path)
    assert {"work_runs", "run_events", "interactions", "schema_meta"} <= _tables(path)
    assert schema_version(path) == SCHEMA_VERSION
    assert list_payloads("conversations", path)[0]["title"] == "Kept"
    init_db(path)  # idempotent
    assert schema_version(path) == SCHEMA_VERSION


def test_newer_schema_is_refused_and_left_untouched(tmp_path):
    path = tmp_path / "future.sqlite3"
    init_db(path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE schema_meta SET value = '99' WHERE key = 'schema_version'")
    before = _tables(path)
    with pytest.raises(SchemaVersionError) as exc:
        init_db(path)
    assert exc.value.status == "ERROR_DATABASE_SCHEMA_TOO_NEW"
    assert schema_version(path) == 99
    assert _tables(path) == before


def test_interrupted_upgrade_rolls_back_completely(tmp_path, monkeypatch):
    path = _v1_database(tmp_path / "old.sqlite3")
    monkeypatch.setattr(db, "_SCHEMA_SQL", db._SCHEMA_SQL + ";\nCREATE TABLE broken (")
    with pytest.raises(sqlite3.OperationalError):
        init_db(path)
    assert "work_runs" not in _tables(path)
    assert "schema_meta" not in _tables(path)
    assert list_payloads("conversations", path)[0]["title"] == "Kept"
    monkeypatch.undo()
    init_db(path)
    assert "work_runs" in _tables(path)


def test_app_refuses_to_start_on_a_newer_database(tmp_path):
    from backend.app.main import create_app
    from backend.tests.test_api import make_settings

    cfg = make_settings(tmp_path)
    cfg.ensure_dirs()
    init_db(cfg.sqlite_path)
    with sqlite3.connect(cfg.sqlite_path) as conn:
        conn.execute("UPDATE schema_meta SET value = '99' WHERE key = 'schema_version'")
    with pytest.raises(SchemaVersionError):
        create_app(cfg)
