from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.memory import MemoryError, list_memory_items, remember_memory_item
from backend.app.memory_policy import select_memory_context
from backend.tests.test_api import make_settings


def _row(memory_id: str, content: str, *, scope: str = "global", scope_id: str | None = None) -> dict:
    return {
        "memory_id": memory_id,
        "kind": "preference",
        "content": content,
        "enabled": True,
        "source": "test",
        "scope": scope,
        "scope_id": scope_id,
    }


def test_memory_storage_defaults_legacy_behavior_to_global(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    saved = remember_memory_item("Prefer concise answers.", cfg)["memory"]

    assert saved["scope"] == "global"
    assert saved["scope_id"] is None
    listed = list_memory_items(cfg)["memories"]
    assert listed[0]["scope"] == "global"
    assert listed[0]["scope_id"] is None


@pytest.mark.parametrize(
    ("scope", "scope_id", "status"),
    [
        ("global", "unexpected", "ERROR_MEMORY_SCOPE_ID_FOR_GLOBAL"),
        ("workspace", None, "ERROR_MEMORY_SCOPE_ID_REQUIRED"),
        ("conversation", "", "ERROR_MEMORY_SCOPE_ID_REQUIRED"),
        ("other", None, "ERROR_MEMORY_SCOPE_UNSUPPORTED"),
    ],
)
def test_invalid_scope_bindings_fail_closed(tmp_path: Path, scope: str, scope_id: str | None, status: str) -> None:
    cfg = make_settings(tmp_path)
    with pytest.raises(MemoryError) as exc:
        remember_memory_item("Scoped memory.", cfg, scope=scope, scope_id=scope_id)
    assert exc.value.status == status


def test_scope_selection_excludes_nonmatching_rows_and_prefers_specific_duplicates() -> None:
    items = [
        _row("global", "Prefer tables."),
        _row("workspace", "Prefer tables.", scope="workspace", scope_id="ws-1"),
        _row("conversation", "Prefer tables.", scope="conversation", scope_id="conv-1"),
        _row("other-conversation", "Never include me.", scope="conversation", scope_id="conv-2"),
        _row("other-workspace", "Never include me either.", scope="workspace", scope_id="ws-2"),
    ]

    selected = select_memory_context(
        items=items,
        workspace_id="ws-1",
        conversation_id="conv-1",
        max_items=10,
        max_chars=1000,
    )

    assert selected["selected_memory_ids"] == ["conversation"]
    excluded = {(row.get("memory_id"), row["reason"]) for row in selected["excluded"]}
    assert ("other-conversation", "SCOPE_MISMATCH") in excluded
    assert ("other-workspace", "SCOPE_MISMATCH") in excluded
    assert ("workspace", "DUPLICATE_CONTENT") in excluded
    assert ("global", "DUPLICATE_CONTENT") in excluded
    assert selected["invariants"]["all_selected_scopes_match"] is True


def test_scoped_memory_never_leaks_without_matching_context() -> None:
    items = [
        _row("global", "Global."),
        _row("workspace", "Workspace.", scope="workspace", scope_id="ws-1"),
        _row("conversation", "Conversation.", scope="conversation", scope_id="conv-1"),
    ]

    selected = select_memory_context(items=items, max_items=10, max_chars=1000)

    assert selected["selected_memory_ids"] == ["global"]
    assert {(row["memory_id"], row["reason"]) for row in selected["excluded"]} == {
        ("workspace", "SCOPE_MISMATCH"),
        ("conversation", "SCOPE_MISMATCH"),
    }


def test_api_answer_selection_respects_global_workspace_and_conversation_scope(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    client = TestClient(create_app(cfg))
    ingest = client.post(
        "/ingest",
        files={"upload": ("source.md", b"Source lineage requires cited chunk IDs.", "text/markdown")},
    )
    assert ingest.status_code == 200
    conv_a = client.post("/conversations", json={"title": "A"}).json()["conversation_id"]
    conv_b = client.post("/conversations", json={"title": "B"}).json()["conversation_id"]

    created = [
        client.post("/memory", json={"content": "Global preference.", "kind": "preference"}),
        client.post(
            "/memory",
            json={"content": "Workspace preference.", "kind": "preference", "scope": "workspace", "scope_id": "ws-1"},
        ),
        client.post(
            "/memory",
            json={"content": "Conversation A preference.", "kind": "preference", "scope": "conversation", "scope_id": conv_a},
        ),
        client.post(
            "/memory",
            json={"content": "Conversation B preference.", "kind": "preference", "scope": "conversation", "scope_id": conv_b},
        ),
    ]
    assert all(response.status_code == 200 for response in created)
    ids = [response.json()["memory"]["memory_id"] for response in created]

    direct = client.post(
        "/ask",
        json={"query": "What does source lineage require?", "model_provider": "mock", "workspace_id": "ws-1"},
    )
    assert direct.status_code == 200
    assert direct.json()["memory_ids"] == ids[:2]

    conversation = client.post(
        f"/conversations/{conv_a}/ask",
        json={"query": "What does source lineage require?", "model_provider": "mock", "workspace_id": "ws-1"},
    )
    assert conversation.status_code == 200
    selected_ids = conversation.json()["answer"]["memory_ids"]
    assert ids[0] in selected_ids
    assert ids[1] in selected_ids
    assert ids[2] in selected_ids
    assert ids[3] not in selected_ids
