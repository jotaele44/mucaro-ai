from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app import work_runs
from backend.app.conversations import append_turns, create_conversation, get_conversation
from backend.app.ingestion import ingest_file
from backend.app.main import create_app
from backend.app.work_runs import submit_message
from backend.tests.test_api import make_settings


@pytest.fixture(autouse=True)
def inline_execution():
    work_runs.set_execution_mode("inline")
    yield
    work_runs.set_execution_mode("thread")


def test_legacy_message_edit_is_revisioned_and_preserves_canonical_turn(tmp_path):
    cfg = make_settings(tmp_path)
    cfg.ensure_dirs()
    conversation_id = create_conversation("Edit contract", cfg)["conversation_id"]
    append_turns(
        conversation_id,
        [
            {
                "turn_id": "turn_original_user",
                "role": "user",
                "content": "Original user text",
                "created_at": "2026-10-01T00:00:00Z",
                "status": "USER_MESSAGE",
            }
        ],
        cfg,
    )
    client = TestClient(create_app(cfg))

    edited = client.patch(
        f"/conversations/{conversation_id}/messages/turn:turn_original_user",
        json={"content": "Revised display text", "expected_revision": 0},
    )
    assert edited.status_code == 200
    row = edited.json()["message_edit"]
    assert row["scope"] == "DISPLAY_OVERRIDE"
    assert row["original_preserved"] is True
    assert row["revision"] == 1
    assert row["content"] == "Revised display text"

    stored = get_conversation(conversation_id, cfg)
    assert stored["turns"][0]["content"] == "Original user text"
    assert stored["message_edits"]["turn:turn_original_user"]["content"] == "Revised display text"

    conflict = client.patch(
        f"/conversations/{conversation_id}/messages/turn:turn_original_user",
        json={"content": "Stale writer", "expected_revision": 0},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["status"] == "ERROR_MESSAGE_EDIT_REVISION_CONFLICT"

    second = client.patch(
        f"/conversations/{conversation_id}/messages/turn:turn_original_user",
        json={"content": "Second revision", "expected_revision": 1},
    )
    assert second.status_code == 200
    assert second.json()["message_edit"]["revision"] == 2


def test_run_prompt_and_answer_edits_are_bound_to_the_owning_conversation(tmp_path):
    cfg = make_settings(tmp_path)
    cfg.ensure_dirs()
    note = tmp_path / "lineage.md"
    note.write_text(
        "Source lineage binds every chunk to a SHA-256 source identity and a stable chunk id.",
        encoding="utf-8",
    )
    ingest_file(note, cfg)
    conversation_id = create_conversation("Run edits", cfg)["conversation_id"]
    run = submit_message(
        conversation_id,
        "How does source lineage work?",
        mode="ask",
        options={"model_provider": "mock", "retrieval_strategy": "persistent"},
        cfg=cfg,
    )["run"]
    run_id = run["run_id"]
    canonical_before = list(get_conversation(conversation_id, cfg)["turns"])
    client = TestClient(create_app(cfg))

    user_edit = client.patch(
        f"/conversations/{conversation_id}/messages/run:{run_id}:user",
        json={"content": "Edited prompt display", "expected_revision": 0},
    )
    assistant_edit = client.patch(
        f"/conversations/{conversation_id}/messages/run:{run_id}:assistant",
        json={"content": "Edited answer display", "expected_revision": 0},
    )

    assert user_edit.status_code == 200
    assert assistant_edit.status_code == 200
    stored = get_conversation(conversation_id, cfg)
    assert stored["turns"] == canonical_before
    assert stored["message_edits"][f"run:{run_id}:user"]["content"] == "Edited prompt display"
    assert stored["message_edits"][f"run:{run_id}:assistant"]["content"] == "Edited answer display"

    other_id = create_conversation("Other", cfg)["conversation_id"]
    cross_conversation = client.patch(
        f"/conversations/{other_id}/messages/run:{run_id}:user",
        json={"content": "Wrong owner", "expected_revision": 0},
    )
    assert cross_conversation.status_code == 404
    assert cross_conversation.json()["detail"]["status"] == "ERROR_MESSAGE_NOT_FOUND"


def test_message_edit_rejects_unknown_keys_empty_content_and_missing_auth(tmp_path):
    cfg = make_settings(tmp_path)
    cfg.ensure_dirs()
    conversation_id = create_conversation("Negative gates", cfg)["conversation_id"]
    client = TestClient(create_app(cfg))

    missing = client.patch(
        f"/conversations/{conversation_id}/messages/turn:missing",
        json={"content": "No target", "expected_revision": 0},
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["status"] == "ERROR_MESSAGE_NOT_FOUND"

    invalid = client.patch(
        f"/conversations/{conversation_id}/messages/not-a-message-key",
        json={"content": "No target", "expected_revision": 0},
    )
    assert invalid.status_code == 400
    assert invalid.json()["detail"]["status"] == "ERROR_MESSAGE_KEY_INVALID"

    append_turns(
        conversation_id,
        [
            {
                "turn_id": "turn_empty_gate",
                "role": "assistant",
                "content": "Original",
                "created_at": "2026-10-01T00:00:00Z",
                "status": "DONE",
            }
        ],
        cfg,
    )
    empty = client.patch(
        f"/conversations/{conversation_id}/messages/turn:turn_empty_gate",
        json={"content": "   ", "expected_revision": 0},
    )
    assert empty.status_code == 400
    assert empty.json()["detail"]["status"] == "ERROR_MESSAGE_EDIT_EMPTY"

    protected_cfg = make_settings(tmp_path / "protected", operator_token="secret-token")
    protected_cfg.ensure_dirs()
    protected_id = create_conversation("Protected", protected_cfg)["conversation_id"]
    append_turns(
        protected_id,
        [
            {
                "turn_id": "turn_protected",
                "role": "user",
                "content": "Original",
                "created_at": "2026-10-01T00:00:00Z",
                "status": "USER_MESSAGE",
            }
        ],
        protected_cfg,
    )
    protected_client = TestClient(create_app(protected_cfg))
    denied = protected_client.patch(
        f"/conversations/{protected_id}/messages/turn:turn_protected",
        json={"content": "Unauthorized", "expected_revision": 0},
    )
    assert denied.status_code == 401
    allowed = protected_client.patch(
        f"/conversations/{protected_id}/messages/turn:turn_protected",
        json={"content": "Authorized", "expected_revision": 0},
        headers={"X-Mucaro-Token": "secret-token"},
    )
    assert allowed.status_code == 200
