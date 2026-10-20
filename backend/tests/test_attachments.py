"""Files attached to a chat message: upload checks, what Claude receives, and what the history keeps."""

from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

from backend.app import attachments as attachments_module
from backend.app.agent_store import load_history, load_history_rows
from backend.app.app_settings import save_app_settings
from backend.app.attachments import AttachmentError, save_attachment
from backend.app.conversations import create_conversation, delete_conversation, get_conversation
from backend.app.main import create_app
from backend.app.work_runs import RunError, get_run, list_run_events, submit_message
from backend.tests.test_agent import api, cfg, chat, inline_execution, message, text  # noqa: F401 - pytest fixtures

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC")
PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"


def upload(client: TestClient, chat_id: str, name: str, data: bytes):
    return client.post(f"/conversations/{chat_id}/attachments", files={"upload": (name, data)})


def test_uploads_are_checked_and_described(cfg, chat):
    client = TestClient(create_app(cfg))
    image = upload(client, chat, "photo.png", PNG).json()["attachment"]
    assert image["kind"] == "image" and image["media_type"] == "image/png" and image["name"] == "photo.png"
    pdf = upload(client, chat, "report.pdf", PDF).json()["attachment"]
    assert pdf["kind"] == "pdf"
    notes = upload(client, chat, "../../notes.md", b"# Plan\nShip on Friday.").json()["attachment"]
    assert notes["kind"] == "document" and notes["name"] == "notes.md"

    refused = upload(client, chat, "tool.exe", b"MZ\x90\x00")
    assert refused.status_code == 422 and refused.json()["detail"]["status"] == "ERROR_ATTACHMENT_UNSUPPORTED"
    assert upload(client, chat, "empty.txt", b"").json()["detail"]["status"] == "ERROR_ATTACHMENT_EMPTY"
    locked = upload(client, chat, "locked.pdf", b"%PDF-1.4\n/Encrypt 5 0 R\n%%EOF")
    assert locked.json()["detail"]["status"] == "ERROR_ATTACHMENT_ENCRYPTED"
    assert upload(client, "conv_missing", "a.png", PNG).status_code == 404
    listed = client.get(f"/conversations/{chat}/attachments").json()["attachments"]
    assert [row["name"] for row in listed] == ["photo.png", "report.pdf", "notes.md"]


def test_size_and_conversation_limits(cfg, chat, monkeypatch):
    monkeypatch.setattr(attachments_module, "MAX_IMAGE_BYTES", 10)
    with pytest.raises(AttachmentError) as too_big:
        save_attachment(chat, "big.png", PNG, cfg)
    assert too_big.value.status == "ERROR_ATTACHMENT_TOO_LARGE"
    monkeypatch.setattr(attachments_module, "MAX_IMAGE_BYTES", 7 * 1024 * 1024)
    monkeypatch.setattr(attachments_module, "MAX_CONVERSATION_ENCODED_BYTES", 150)
    save_attachment(chat, "a.png", PNG, cfg)
    with pytest.raises(AttachmentError) as full:
        save_attachment(chat, "b.png", PNG, cfg)
    assert full.value.status == "ERROR_ATTACHMENT_LIMIT"
    assert len(list((cfg.data_dir / "attachments" / chat).iterdir())) == 1  # the refused file left nothing behind


def test_claude_receives_files_and_history_keeps_references(cfg, api, chat):
    image = save_attachment(chat, "chart.png", PNG, cfg)
    notes = save_attachment(chat, "notes.md", b"# Notes\nThe launch is on Friday.", cfg)
    pdf = save_attachment(chat, "report.pdf", PDF, cfg)
    citation = {"type": "char_location", "cited_text": "The launch is on Friday.", "document_index": 0, "document_title": "notes.md",
                "start_char_index": 8, "end_char_index": 32}
    api.script = [message([text("It launches on Friday.", [citation])]), message([text("Still Friday.")])]

    run = submit_message(chat, "When is the launch?", mode="agent", attachment_ids=[image["attachment_id"], notes["attachment_id"], pdf["attachment_id"]], cfg=cfg)["run"]

    assert get_run(run["run_id"], cfg)["state"] == "COMPLETED"
    sent = api.requests[0]["messages"][-1]["content"]
    assert [block["type"] for block in sent] == ["text", "image", "text", "document", "text", "document", "text", "text"]
    assert sent[0]["text"] == "Attachment 1: chart.png"
    assert base64.b64decode(sent[1]["source"]["data"]) == PNG and sent[1]["source"]["media_type"] == "image/png"
    assert sent[3]["source"] == {"type": "text", "media_type": "text/plain", "data": "# Notes\nThe launch is on Friday."}
    assert sent[3]["citations"] == {"enabled": True} and sent[3]["title"] == "notes.md"
    assert sent[5]["source"]["media_type"] == "application/pdf" and sent[5]["citations"] == {"enabled": True}
    assert sent[-1]["text"] == "When is the launch?"

    stored = json.loads(load_history_rows(chat, cfg)[0]["content_json"])
    assert stored[1] == {"type": "mucaro_attachment", "attachment_id": image["attachment_id"], "name": "chart.png", "kind": "image"}
    assert "base64" not in load_history_rows(chat, cfg)[0]["content_json"]

    cites = [row["data"] for row in list_run_events(run["run_id"], cfg=cfg) if row["type"] == "citation"]
    assert cites == [{"citation_order": 1, "title": "notes.md", "text_snippet": "The launch is on Friday.", "kind": "attachment"}]
    user_turn = get_conversation(chat, cfg)["turns"][0]
    assert [row["name"] for row in user_turn["attachments"]] == ["chart.png", "notes.md", "report.pdf"]

    # The next turn resends the same files, expanded from the same bytes: the prefix is identical.
    submit_message(chat, "And the time?", mode="agent", cfg=cfg)
    assert api.requests[1]["messages"][0] == api.requests[0]["messages"][-1]


def test_attachments_need_claude_and_this_conversation(cfg, api, chat):
    image = save_attachment(chat, "a.png", PNG, cfg)
    with pytest.raises(RunError) as planned:
        submit_message(chat, "look", mode="plan", attachment_ids=[image["attachment_id"]], cfg=cfg)
    assert planned.value.status == "ERROR_ATTACHMENTS_NEED_CLAUDE"
    save_app_settings({"live_consent": False}, cfg)
    with pytest.raises(RunError) as offline:
        submit_message(chat, "look", mode="agent", attachment_ids=[image["attachment_id"]], cfg=cfg)
    assert offline.value.status == "ERROR_ATTACHMENTS_NEED_CLAUDE"
    save_app_settings({"live_consent": True}, cfg)

    other = create_conversation("Other", cfg)["conversation_id"]
    with pytest.raises(RunError) as foreign:
        submit_message(other, "look", mode="agent", attachment_ids=[image["attachment_id"]], cfg=cfg)
    assert foreign.value.status == "ERROR_ATTACHMENT_NOT_FOUND"
    with pytest.raises(RunError) as forged:
        submit_message(chat, "look", mode="agent", attachment_ids=["att_../../etc"], cfg=cfg)
    assert forged.value.status == "ERROR_ATTACHMENT_NOT_FOUND"
    assert api.requests == []


def test_files_are_served_safely_and_deleted_with_the_chat(cfg, api, chat):
    client = TestClient(create_app(cfg))
    image = save_attachment(chat, "a.png", PNG, cfg)
    page = save_attachment(chat, "page.html", b"<script>alert(1)</script><p>hi</p>", cfg)
    shown = client.get(f"/conversations/{chat}/attachments/{image['attachment_id']}/file")
    assert shown.status_code == 200 and shown.headers["content-type"] == "image/png" and shown.content == PNG
    downloaded = client.get(f"/conversations/{chat}/attachments/{page['attachment_id']}/file")
    assert downloaded.headers["content-type"] == "application/octet-stream"
    assert downloaded.headers["content-disposition"].startswith("attachment")

    api.script = [message([text("ok")])]
    submit_message(chat, "hi", mode="agent", attachment_ids=[image["attachment_id"]], cfg=cfg)
    delete_conversation(chat, cfg)
    assert not (cfg.data_dir / "attachments" / chat).exists()
    assert load_history(chat, cfg) == []


def test_a_file_missing_at_replay_becomes_a_note(cfg, api, chat):
    image = save_attachment(chat, "gone.png", PNG, cfg)
    api.script = [message([text("ok")]), message([text("ok")])]
    submit_message(chat, "first", mode="agent", attachment_ids=[image["attachment_id"]], cfg=cfg)
    attachments_module.delete_conversation_attachments(chat, cfg)
    submit_message(chat, "second", mode="agent", cfg=cfg)
    replayed = api.requests[1]["messages"][0]["content"][1]
    assert replayed == {"type": "text", "text": "[Attachment gone.png is no longer available.]"}
