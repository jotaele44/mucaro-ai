"""Claude settings: key storage, consent and the Settings API. The Keychain is never touched here."""

from __future__ import annotations

import json
import stat

import pytest
from fastapi.testclient import TestClient

from backend.app import app_settings
from backend.app.app_settings import (
    AppSettingsError,
    claude_status,
    delete_api_key,
    get_api_key,
    load_app_settings,
    save_app_settings,
    store_api_key,
    validate_api_key,
)
from backend.app.main import create_app
from backend.app.permissions import grant_directory
from backend.app.tools import approve_tool_call, request_tool_call, run_tool_call
from backend.tests.test_api import make_settings

KEY = "sk-ant-api03-" + "k" * 60


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    # The Anthropic SDK is an optional extra (CI installs only "dev"); these tests never need it.
    monkeypatch.setattr("backend.app.app_settings.sdk_available", lambda: True)
    monkeypatch.setenv("MUCARO_AI_SECRET_STORE", "file")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MUCARO_AI_LIVE_MODE", raising=False)
    settings = make_settings(tmp_path)
    settings.ensure_dirs()
    return settings


def test_defaults_and_validation(cfg):
    values = load_app_settings(cfg)
    assert values == {"model": "claude-opus-5-5", "effort": "medium", "web_tools": True, "code_sandbox": True, "live_consent": False}
    with pytest.raises(AppSettingsError):
        save_app_settings({"model": "gpt-5"}, cfg)
    with pytest.raises(AppSettingsError):
        save_app_settings({"effort": "turbo"}, cfg)
    assert save_app_settings({"model": "claude-sonnet-5-5", "effort": "low"}, cfg)["model"] == "claude-sonnet-5-5"


@pytest.mark.parametrize("bad", ["", "hello", "sk-ant-oat01-" + "x" * 40, "sk-ant-api03- has spaces " + "x" * 20])
def test_key_shape_is_checked(bad):
    with pytest.raises(AppSettingsError):
        validate_api_key(bad)


def test_file_store_round_trip_is_private(cfg):
    assert store_api_key(KEY, cfg) == "file"
    path = cfg.processed_dir / app_settings.SECRETS_NAME
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert get_api_key(cfg) == (KEY, "file")

    delete_api_key(cfg)
    assert get_api_key(cfg) == (None, None)
    assert not path.exists()


def test_environment_key_is_a_fallback_and_status_never_reveals_it(cfg, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    status = claude_status(cfg)
    assert status["key_source"] == "env" and status["key_hint"] == f"…{KEY[-4:]}"
    assert status["status"] == "CLAUDE_CONSENT_REQUIRED" and status["ready"] is False
    assert KEY not in json.dumps(status)

    save_app_settings({"live_consent": True}, cfg)
    assert claude_status(cfg)["ready"] is True


def test_settings_api_saves_settings_and_keys_without_echoing_them(cfg):
    client = TestClient(create_app(cfg))
    assert client.get("/settings/claude").json()["status"] == "CLAUDE_KEY_MISSING"

    saved = client.put("/settings/claude/key", json={"api_key": KEY})
    assert saved.status_code == 200 and saved.json()["key_present"] is True
    assert KEY not in saved.text

    updated = client.put("/settings/claude", json={"live_consent": True, "effort": "high"})
    assert updated.json()["ready"] is True and updated.json()["effort"] == "high"
    assert client.put("/settings/claude/key", json={"api_key": "nope"}).json()["detail"]["status"] == "ERROR_CLAUDE_KEY_INVALID"
    assert client.delete("/settings/claude/key").json()["key_present"] is False


def test_keys_cannot_be_set_from_another_device(cfg):
    client = TestClient(create_app(cfg), client=("192.168.1.20", 50000))
    response = client.put("/settings/claude/key", json={"api_key": KEY}, headers={"Authorization": "Bearer unused"})
    assert response.status_code in {401, 403}
    assert get_api_key(cfg) == (None, None)


# --- the governed tools behind Claude's client tools -------------------------------------------


def test_source_tools_search_and_read(cfg, tmp_path):
    from backend.app.ingestion import ingest_file

    note = tmp_path / "plan.md"
    note.write_text("The rollout starts in March. The budget is fixed.", encoding="utf-8")
    ingest_file(note, cfg)

    found = request_tool_call("sources.search", {"query": "rollout March"}, cfg=cfg, requested_by="test")["tool_call"]
    approve_tool_call(found["tool_call_id"], cfg=cfg, approved_by="test")
    result = run_tool_call(found["tool_call_id"], cfg=cfg, executed_by="test")["result"]
    assert result["status"] == "SOURCE_SEARCH_OK" and "March" in result["results"][0]["text"]

    source_id = result["results"][0]["source_id"]
    read = request_tool_call("sources.read", {"source_id": source_id}, cfg=cfg, requested_by="test")["tool_call"]
    approve_tool_call(read["tool_call_id"], cfg=cfg, approved_by="test")
    document = run_tool_call(read["tool_call_id"], cfg=cfg, executed_by="test")["result"]
    assert document["file_name"] == "plan.md" and "budget" in document["chunks"][0]["text"]


def test_memory_remember_saves_an_item(cfg):
    call = request_tool_call("memory.remember", {"content": "Likes concise answers", "kind": "preference"}, cfg=cfg, requested_by="test")["tool_call"]
    assert call["status"] == "PENDING_APPROVAL"
    approve_tool_call(call["tool_call_id"], cfg=cfg, approved_by="test")
    saved = run_tool_call(call["tool_call_id"], cfg=cfg, executed_by="test")["result"]
    assert saved["memory"]["content"] == "Likes concise answers" and saved["memory"]["source"] == "assistant"


def test_file_tools_stay_inside_granted_folders(cfg, tmp_path_factory):
    granted = tmp_path_factory.mktemp("granted")
    (granted / "a.txt").write_text("alpha", encoding="utf-8")
    grant_directory(granted, cfg, granted_by="test")

    listing = request_tool_call("files.list_directory", {"path": str(granted)}, cfg=cfg, requested_by="test")["tool_call"]
    approve_tool_call(listing["tool_call_id"], cfg=cfg, approved_by="test")
    entries = run_tool_call(listing["tool_call_id"], cfg=cfg, executed_by="test")["result"]["entries"]
    assert entries == [{"name": "a.txt", "type": "file", "bytes": 5}]

    read = request_tool_call("files.read_file", {"path": str(granted / "a.txt")}, cfg=cfg, requested_by="test")["tool_call"]
    approve_tool_call(read["tool_call_id"], cfg=cfg, approved_by="test")
    assert run_tool_call(read["tool_call_id"], cfg=cfg, executed_by="test")["result"]["text"] == "alpha"

    outside = request_tool_call("files.read_file", {"path": str(cfg.data_dir / "processed" / "test.sqlite3")}, cfg=cfg, requested_by="test")["tool_call"]
    approve_tool_call(outside["tool_call_id"], cfg=cfg, approved_by="test")
    with pytest.raises(Exception) as refused:
        run_tool_call(outside["tool_call_id"], cfg=cfg, executed_by="test")
    assert getattr(refused.value, "status", "") in {"ERROR_FILE_PERMISSION_REQUIRED", "ERROR_FILE_NOT_FOUND"}
