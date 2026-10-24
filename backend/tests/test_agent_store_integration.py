"""Isolated Claude message-storage schema and replay gates."""
from __future__ import annotations

import sqlite3

import pytest

from backend.app.agent_store import (
    append_messages,
    delete_agent_conversation,
    load_history,
    load_history_rows,
    save_agent_conversation,
    get_agent_conversation,
)
from backend.app.db import SchemaVersionError, init_db, schema_version
from backend.tests.test_api import make_settings


def test_agent_storage_v3_append_only_and_delete(tmp_path):
    cfg = make_settings(tmp_path)
    init_db(cfg.sqlite_path)
    assert schema_version(cfg.sqlite_path) == 3
    payload = [{"type": "text", "text": "Original bytes"}]
    assert append_messages("conv1", [{"role": "user", "content": payload, "run_id": "run1"}], cfg) == 1
    assert append_messages("conv1", [{"role": "assistant", "content": [{"type": "text", "text": "Reply"}]}], cfg) == 1
    assert [row["role"] for row in load_history("conv1", cfg)] == ["user", "assistant"]
    assert [row["seq"] for row in load_history_rows("conv1", cfg)] == [1, 2]
    save_agent_conversation("conv1", {"label": "review"}, cfg)
    assert get_agent_conversation("conv1", cfg) == {"label": "review"}
    delete_agent_conversation("conv1", cfg)
    assert load_history("conv1", cfg) == []
    assert get_agent_conversation("conv1", cfg) is None


def test_agent_append_rollback_on_invalid_role(tmp_path):
    cfg = make_settings(tmp_path)
    init_db(cfg.sqlite_path)
    with pytest.raises(ValueError, match="role must"):
        append_messages("conv1", [
            {"role": "user", "content": ["ok"]},
            {"role": "system", "content": ["not authorized"]},
        ], cfg)
    assert load_history("conv1", cfg) == []


def test_future_schema_refused_without_downgrade(tmp_path):
    cfg = make_settings(tmp_path)
    init_db(cfg.sqlite_path)
    with sqlite3.connect(cfg.sqlite_path) as con:
        con.execute("UPDATE schema_meta SET value='999' WHERE key='schema_version'")
    with pytest.raises(SchemaVersionError):
        init_db(cfg.sqlite_path)
    assert schema_version(cfg.sqlite_path) == 999
