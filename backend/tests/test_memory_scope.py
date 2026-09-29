from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.memory import (
    MemoryError,
    current_workspace_scope_id,
    list_memory_items,
    remember_memory_item,
    set_memory_enabled,
)
from backend.app.memory_policy import select_memory_context


def cfg_for(root: Path) -> Settings:
    return Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "test.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )


def test_new_memory_defaults_to_explicit_global_scope(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    row = remember_memory_item("Prefer source IDs.", cfg)["memory"]

    assert row["scope"] == "global"
    assert row["scope_id"] is None


def test_workspace_scope_defaults_to_current_stable_workspace_id(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    row = remember_memory_item("Prefer maps.", cfg, scope="workspace")["memory"]

    assert row["scope"] == "workspace"
    assert row["scope_id"] == current_workspace_scope_id(cfg)
    assert row["scope_id"].startswith("workspace_")


def test_scope_combinations_fail_closed(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)

    with pytest.raises(MemoryError) as conversation:
        remember_memory_item("Conversation note.", cfg, scope="conversation")
    assert conversation.value.status == "ERROR_MEMORY_CONVERSATION_SCOPE_REQUIRED"

    with pytest.raises(MemoryError) as global_id:
        remember_memory_item("Global note.", cfg, scope="global", scope_id="conv_wrong")
    assert global_id.value.status == "ERROR_MEMORY_SCOPE_ID_FOR_GLOBAL"

    with pytest.raises(MemoryError) as unsupported:
        remember_memory_item("Unsupported.", cfg, scope="planet")
    assert unsupported.value.status == "ERROR_MEMORY_SCOPE_UNSUPPORTED"


def test_legacy_rows_are_exposed_as_global_without_rewriting_until_mutation(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    cfg.ensure_dirs()
    path = cfg.processed_dir / "assistant_memory.json"
    original = [
        {
            "memory_id": "mem_abc123",
            "kind": "preference",
            "content": "Legacy preference.",
            "enabled": True,
            "source": "legacy",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    ]
    path.write_text(json.dumps(original), encoding="utf-8")
    frozen_bytes = path.read_bytes()

    listed = list_memory_items(cfg)["memories"][0]
    assert listed["scope"] == "global"
    assert listed["scope_id"] is None
    assert listed["scope_migration"] == "LEGACY_DEFAULT_GLOBAL"
    assert path.read_bytes() == frozen_bytes

    updated = set_memory_enabled("mem_abc123", False, cfg)["memory"]
    assert updated["scope"] == "global"
    assert updated["scope_id"] is None
    persisted = json.loads(path.read_text(encoding="utf-8"))[0]
    assert "scope_migration" not in persisted
    assert persisted["scope"] == "global"
    assert persisted["scope_id"] is None


def scoped_rows(workspace_id: str) -> list[dict]:
    return [
        {"memory_id": "m_global", "kind": "fact", "content": "Global.", "enabled": True, "source": "test", "scope": "global", "scope_id": None},
        {"memory_id": "m_workspace", "kind": "fact", "content": "Workspace.", "enabled": True, "source": "test", "scope": "workspace", "scope_id": workspace_id},
        {"memory_id": "m_other_workspace", "kind": "fact", "content": "Other workspace.", "enabled": True, "source": "test", "scope": "workspace", "scope_id": "workspace_other"},
        {"memory_id": "m_conversation", "kind": "fact", "content": "Conversation.", "enabled": True, "source": "test", "scope": "conversation", "scope_id": "conv_current"},
        {"memory_id": "m_other_conversation", "kind": "fact", "content": "Other conversation.", "enabled": True, "source": "test", "scope": "conversation", "scope_id": "conv_other"},
    ]


def test_scope_selection_closes_row_accounting_and_excludes_mismatches(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    workspace_id = current_workspace_scope_id(cfg)

    report = select_memory_context(
        cfg,
        items=scoped_rows(workspace_id),
        conversation_scope_id="conv_current",
    )

    assert report["selected_memory_ids"] == ["m_global", "m_workspace", "m_conversation"]
    assert report["source_count"] == 5
    assert report["selected_count"] == 3
    assert report["excluded_count"] == 2
    assert report["invariants"]["row_accounting_closed"] is True
    assert report["invariants"]["all_selected_scopes_match"] is True
    assert {(row["memory_id"], row["reason"]) for row in report["excluded"]} == {
        ("m_other_workspace", "SCOPE_MISMATCH"),
        ("m_other_conversation", "SCOPE_MISMATCH"),
    }


def test_conversation_memory_is_never_selected_without_conversation_context(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    workspace_id = current_workspace_scope_id(cfg)

    report = select_memory_context(cfg, items=scoped_rows(workspace_id))

    assert report["selected_memory_ids"] == ["m_global", "m_workspace"]
    conversation_exclusions = [
        row for row in report["excluded"] if row.get("scope") == "conversation"
    ]
    assert len(conversation_exclusions) == 2
    assert {row["reason"] for row in conversation_exclusions} == {"SCOPE_CONTEXT_MISSING"}


def test_api_memory_scope_is_inspectable_and_workspace_scope_is_bound(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    client = TestClient(create_app(cfg))

    workspace = client.post(
        "/memory",
        json={"content": "Workspace preference.", "kind": "preference", "scope": "workspace"},
    )
    assert workspace.status_code == 200
    assert workspace.json()["memory"]["scope"] == "workspace"
    assert workspace.json()["memory"]["scope_id"] == current_workspace_scope_id(cfg)

    conversation = client.post(
        "/memory",
        json={
            "content": "Conversation preference.",
            "kind": "preference",
            "scope": "conversation",
            "scope_id": "conv_example",
        },
    )
    assert conversation.status_code == 200
    assert conversation.json()["memory"]["scope"] == "conversation"
    assert conversation.json()["memory"]["scope_id"] == "conv_example"

    rows = client.get("/memory").json()["memories"]
    assert {(row["scope"], row["scope_id"]) for row in rows} == {
        ("workspace", current_workspace_scope_id(cfg)),
        ("conversation", "conv_example"),
    }
