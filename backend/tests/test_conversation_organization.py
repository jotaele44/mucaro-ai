from __future__ import annotations

import json
import multiprocessing
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from backend.app.cli import main
from backend.app.conversations import (
    ConversationRevisionError,
    append_turns,
    create_conversation,
    get_conversation,
    list_conversations,
    update_conversation,
)
from backend.app.db import upsert_payload
from backend.app.main import create_app
from backend.tests.test_api import make_settings


def test_organization_persists_and_is_idempotent_across_restarts(tmp_path):
    cfg = make_settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        created = client.post("/conversations", json={"title": "Research"}).json()
        cid = created["conversation_id"]
        pinned = client.patch(f"/conversations/{cid}", json={"pinned": True, "expected_revision": 0})
        assert pinned.status_code == 200
        assert pinned.json()["revision"] == 1
        replay = client.patch(f"/conversations/{cid}", json={"pinned": True, "expected_revision": 1})
        assert replay.json() == pinned.json()
        archived = client.patch(f"/conversations/{cid}", json={"archived": True, "expected_revision": 1})
        assert archived.status_code == 200
        assert archived.json()["pinned"] is True
        assert archived.json()["archived"] is True
    with TestClient(create_app(make_settings(tmp_path))) as restarted:
        row = restarted.get("/conversations").json()["conversations"][0]
        assert row["archived"] is True and row["pinned"] is True and row["revision"] == 2
        restored = restarted.patch(f"/conversations/{cid}", json={"archived": False, "expected_revision": 2})
        assert restored.json()["revision"] == 3
        assert restored.json()["turns"] == []


@pytest.mark.parametrize("changes", [
    {}, {"expected_revision": 0}, {"pinned": "false"}, {"archived": 1},
    {"pinned": None}, {"title": None}, {"title": ""}, {"unknown": True},
    {"pinned": True, "expected_revision": -1}, {"pinned": True, "expected_revision": True},
])
def test_invalid_updates_do_not_mutate(tmp_path, changes):
    cfg = make_settings(tmp_path)
    created = create_conversation("Original", cfg)
    with TestClient(create_app(cfg)) as client:
        response = client.patch(f"/conversations/{created['conversation_id']}", json=changes)
        assert response.status_code == 422
    assert get_conversation(created["conversation_id"], cfg) == created


def test_auth_missing_target_and_stale_revision_fail_closed(tmp_path):
    cfg = make_settings(tmp_path, operator_token="organization-token")
    created = create_conversation("Original", cfg)
    cid = created["conversation_id"]
    headers = {"X-Mucaro-Token": "organization-token"}
    with TestClient(create_app(cfg)) as client:
        assert client.patch(f"/conversations/{cid}", json={"archived": True}).status_code == 401
        assert client.patch("/conversations/missing", json={"pinned": True}, headers=headers).status_code == 404
        assert client.patch(f"/conversations/{cid}", json={"title": "   "}, headers=headers).status_code == 400
        assert client.patch(f"/conversations/{cid}", json={"title": "Renamed"}, headers=headers).status_code == 200
        conflict = client.patch(f"/conversations/{cid}", json={"pinned": True, "expected_revision": 0}, headers=headers)
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["current_revision"] == 1
    assert get_conversation(cid, cfg)["title"] == "Renamed"
    assert get_conversation(cid, cfg)["pinned"] is False


def test_legacy_rows_and_timestamp_ties_have_stable_order(tmp_path):
    cfg = make_settings(tmp_path)
    create_conversation("Initialize", cfg)
    from backend.app.db import delete_payload
    for row in list_conversations(cfg):
        delete_payload("conversations", "conversation_id", row["conversation_id"], cfg.sqlite_path)
    for cid, pinned, archived in [("conv_z", False, False), ("conv_b", True, False), ("conv_a", True, False), ("conv_c", True, True)]:
        upsert_payload("conversations", "conversation_id", {
            "conversation_id": cid, "title": cid, "turns": [], "updated_at": "2026-10-07T00:00:00Z",
            **({"pinned": pinned, "archived": archived} if cid != "conv_z" else {}),
        }, cfg.sqlite_path)
    assert [row["conversation_id"] for row in list_conversations(cfg)] == ["conv_a", "conv_b", "conv_z", "conv_c"]
    updated = update_conversation("conv_z", {"pinned": True}, cfg, expected_revision=0)
    assert updated["revision"] == 1


def test_creation_with_identical_title_and_frozen_time_does_not_overwrite(tmp_path, monkeypatch):
    cfg = make_settings(tmp_path)
    monkeypatch.setattr("backend.app.conversations.utc_now", lambda: "2026-10-07T00:00:00Z")
    ids = [create_conversation("Same title", cfg)["conversation_id"] for _ in range(20)]
    assert len(set(ids)) == len(list_conversations(cfg)) == 20


def _process_writer(root, cid, field, barrier):
    cfg = make_settings(root)
    barrier.wait(timeout=20)
    for index in range(12):
        if field == "turns":
            append_turns(cid, [{"turn_id": f"turn_{index}", "role": "user", "content": str(index)}], cfg)
        else:
            update_conversation(cid, {field: True}, cfg)


def test_parallel_process_writes_preserve_turns_and_organization(tmp_path):
    cfg = make_settings(tmp_path)
    cid = create_conversation("Concurrent", cfg)["conversation_id"]
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(3)
    workers = [ctx.Process(target=_process_writer, args=(tmp_path, cid, field, barrier)) for field in ["turns", "pinned", "archived"]]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=40)
            assert worker.exitcode == 0
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)
    row = get_conversation(cid, cfg)
    assert len(row["turns"]) == 12
    assert row["pinned"] is True and row["archived"] is True
    assert row["revision"] == 14


def test_two_stale_writers_cannot_both_win(tmp_path):
    cfg = make_settings(tmp_path)
    cid = create_conversation("Concurrent revisions", cfg)["conversation_id"]
    def write(field):
        try:
            update_conversation(cid, {field: True}, cfg, expected_revision=0)
            return "PASS"
        except ConversationRevisionError:
            return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, ["pinned", "archived"])) == ["CONFLICT", "PASS"]
    assert get_conversation(cid, cfg)["revision"] == 1


def test_turn_replay_and_invalid_input_preserve_revision(tmp_path):
    cfg = make_settings(tmp_path)
    cid = create_conversation("Replay", cfg)["conversation_id"]
    turn = {"turn_id": "turn_unique", "role": "user", "content": "Original"}
    saved = append_turns(cid, [turn, turn], cfg)
    assert len(saved["turns"]) == 1 and saved["revision"] == 1
    assert append_turns(cid, [turn], cfg) == saved
    with pytest.raises(ValueError, match="ERROR_TURN_ID_CONFLICT"):
        append_turns(cid, [{"turn_id": "new", "content": "Must roll back"}, {**turn, "content": "Conflict"}], cfg)
    assert get_conversation(cid, cfg) == saved
    with pytest.raises(ValueError, match="ERROR_TURN_ID_CONFLICT"):
        append_turns(cid, [{"turn_id": "batch", "content": "One"}, {"turn_id": "batch", "content": "Two"}], cfg)
    assert get_conversation(cid, cfg) == saved
    with pytest.raises(ValueError, match="ERROR_TURN_ID_REQUIRED"):
        append_turns(cid, [{"turn_id": "valid", "content": "Must roll back"}, {"content": "No id"}], cfg)
    assert get_conversation(cid, cfg) == saved


def test_deleted_conversation_is_not_resurrected_by_a_writer(tmp_path):
    from backend.app.conversations import delete_conversation
    cfg = make_settings(tmp_path)
    cid = create_conversation("Deleted", cfg)["conversation_id"]
    assert delete_conversation(cid, cfg)["removed"] == 1
    with pytest.raises(KeyError):
        append_turns(cid, [{"turn_id": "late", "content": "Late answer"}], cfg)
    with pytest.raises(KeyError):
        update_conversation(cid, {"archived": True}, cfg)
    assert get_conversation(cid, cfg) is None


@pytest.mark.parametrize("revision", [True, -1, "0", 0.0])
def test_service_revision_validation_matches_api(tmp_path, revision):
    cfg = make_settings(tmp_path)
    row = create_conversation("Strict revision", cfg)
    with pytest.raises(ValueError, match="ERROR_CONVERSATION_REVISION_INVALID"):
        update_conversation(row["conversation_id"], {"pinned": True}, cfg, expected_revision=revision)
    assert get_conversation(row["conversation_id"], cfg) == row


def test_cli_organization_uses_shared_persistence(tmp_path, capsys):
    cfg = make_settings(tmp_path)
    cid = create_conversation("CLI", cfg)["conversation_id"]
    for command, field, value in [("pin", "pinned", True), ("archive", "archived", True), ("restore", "archived", False), ("unpin", "pinned", False)]:
        assert main(["conversations", command, cid], cfg=cfg) == 0
        assert json.loads(capsys.readouterr().out)[field] is value
        assert get_conversation(cid, cfg)[field] is value
    assert main(["conversations", "archive", "missing"], cfg=cfg) == 2
    assert "ERROR_CONVERSATION_NOT_FOUND" in capsys.readouterr().out
    assert main(["conversations", "pin", cid, "--format", "text"], cfg=cfg) == 0
    assert "pinned=True archived=False revision=5" in capsys.readouterr().out
    assert main(["conversations", "list", "--format", "text"], cfg=cfg) == 0
    assert "pinned=True archived=False revision=5" in capsys.readouterr().out
