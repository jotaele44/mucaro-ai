from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from backend.app import work_runs
from backend.app.conversations import (
    build_conversation_context,
    create_conversation,
    edit_conversation_message,
    get_conversation,
)
from backend.app.db import upsert_payload
from backend.app.ingestion import ingest_file
from backend.app.main import create_app
from backend.app.work_runs import RunError, branch_run, get_run, list_run_events, submit_message
from backend.tests.test_api import make_settings


@pytest.fixture(autouse=True)
def inline_execution():
    work_runs.set_execution_mode("inline")
    yield
    work_runs.set_execution_mode("thread")


def prepared_cfg(tmp_path, *, operator_token=None):
    cfg = make_settings(tmp_path, operator_token=operator_token)
    cfg.ensure_dirs()
    note = tmp_path / "branch-source.md"
    note.write_text(
        "Source lineage preserves stable source identities and canonical context.",
        encoding="utf-8",
    )
    ingest_file(note, cfg)
    return cfg


def submit(conversation_id, prompt, cfg):
    return submit_message(
        conversation_id,
        prompt,
        mode="ask",
        options={"model_provider": "mock", "retrieval_strategy": "persistent"},
        cfg=cfg,
    )["run"]


def test_semantic_branch_preserves_source_and_uses_only_pre_prompt_context(tmp_path):
    cfg = prepared_cfg(tmp_path)
    conversation_id = create_conversation("Canonical source", cfg)["conversation_id"]
    first = submit(conversation_id, "First canonical question", cfg)
    second = submit(conversation_id, "Second canonical question", cfg)

    edit = edit_conversation_message(
        conversation_id,
        f"run:{second['run_id']}:user",
        "Second visible display revision",
        0,
        cfg,
    )
    assert edit["revision"] == 1
    source_before = deepcopy(get_conversation(conversation_id, cfg))

    result = branch_run(
        conversation_id,
        second["run_id"],
        "Second semantic replacement",
        mode="ask",
        options={"model_provider": "mock", "retrieval_strategy": "persistent"},
        idempotency_key="semantic-branch-0001",
        cfg=cfg,
    )

    assert result["status"] == "RUN_BRANCH_CREATED"
    branch_id = result["conversation"]["conversation_id"]
    branch = get_conversation(branch_id, cfg)
    assert branch is not None
    assert branch["branch_origin"]["kind"] == "SEMANTIC_BRANCH"
    assert branch["branch_origin"]["source_conversation_id"] == conversation_id
    assert branch["branch_origin"]["source_run_id"] == second["run_id"]
    assert branch["branch_origin"]["source_display_revision"] == 1
    assert branch["branch_origin"]["original_preserved"] is True

    # Only the canonical first run is snapshotted as context. The source prompt being replaced
    # and its answer are excluded, and display revisions never enter the semantic context.
    assert branch["branch_context_turns"] == source_before["turns"][:2]
    assert [turn["content"] for turn in branch["branch_context_turns"]] == [
        source_before["turns"][0]["content"],
        source_before["turns"][1]["content"],
    ]
    assert all("Second visible display revision" not in turn["content"] for turn in branch["branch_context_turns"])

    persisted_branch_run = get_run(result["run"]["run_id"], cfg)
    assert persisted_branch_run["lineage"]["source_run_id"] == second["run_id"]
    assert persisted_branch_run["lineage"]["original_preserved"] is True
    assert [turn["role"] for turn in branch["turns"]] == ["user", "assistant"]
    assert branch["turns"][0]["content"] == "Second semantic replacement"

    answer = "".join(
        event["data"].get("text", "")
        for event in list_run_events(persisted_branch_run["run_id"], cfg=cfg)
        if event["type"] == "text_delta"
    )
    assert "Conversation context turns used: 2" in answer

    # Source conversation and its display edit remain byte-logically unchanged by branching.
    assert get_conversation(conversation_id, cfg) == source_before
    assert source_before["turns"][2]["content"] == "Second canonical question"
    assert source_before["message_edits"][f"run:{second['run_id']}:user"]["content"] == "Second visible display revision"


def test_semantic_branch_is_idempotent_and_conflicting_key_fails_closed(tmp_path):
    cfg = prepared_cfg(tmp_path)
    conversation_id = create_conversation("Idempotency source", cfg)["conversation_id"]
    run = submit(conversation_id, "Canonical question", cfg)
    kwargs = {
        "mode": "ask",
        "options": {"model_provider": "mock", "retrieval_strategy": "persistent"},
        "idempotency_key": "semantic-branch-0002",
        "cfg": cfg,
    }

    first = branch_run(conversation_id, run["run_id"], "Replacement prompt", **kwargs)
    again = branch_run(conversation_id, run["run_id"], "Replacement prompt", **kwargs)
    assert first["status"] == "RUN_BRANCH_CREATED"
    assert again["status"] == "RUN_BRANCH_DUPLICATE_RETURNED"
    assert again["conversation"]["conversation_id"] == first["conversation"]["conversation_id"]
    assert again["run"]["run_id"] == first["run"]["run_id"]

    with pytest.raises(RunError) as exc:
        branch_run(conversation_id, run["run_id"], "Different replacement", **kwargs)
    assert exc.value.status == "ERROR_BRANCH_IDEMPOTENCY_CONFLICT"


def test_semantic_branch_rejects_wrong_owner_and_noncompleted_source(tmp_path):
    cfg = prepared_cfg(tmp_path)
    source_id = create_conversation("Source", cfg)["conversation_id"]
    other_id = create_conversation("Other", cfg)["conversation_id"]
    completed = submit(source_id, "Completed question", cfg)

    with pytest.raises(RunError) as exc:
        branch_run(
            other_id,
            completed["run_id"],
            "Wrong owner",
            idempotency_key="semantic-branch-0003",
            cfg=cfg,
        )
    assert exc.value.status == "ERROR_BRANCH_SOURCE_NOT_FOUND"

    waiting = submit_message(source_id, "/status", mode="ask", cfg=cfg)["run"]
    assert waiting["state"] == "AWAITING_APPROVAL"
    with pytest.raises(RunError) as exc:
        branch_run(
            source_id,
            waiting["run_id"],
            "Cannot branch active work",
            idempotency_key="semantic-branch-0004",
            cfg=cfg,
        )
    assert exc.value.status == "ERROR_BRANCH_SOURCE_NOT_COMPLETED"


def test_branch_context_corruption_fails_closed(tmp_path):
    cfg = prepared_cfg(tmp_path)
    conversation = create_conversation("Corruption gate", cfg)
    conversation["branch_context_turns"] = "not-a-turn-list"
    upsert_payload("conversations", "conversation_id", conversation, cfg.sqlite_path)

    with pytest.raises(ValueError, match="ERROR_BRANCH_CONTEXT_INVALID"):
        build_conversation_context(get_conversation(conversation["conversation_id"], cfg), cfg)  # type: ignore[arg-type]


def test_branch_endpoint_requires_auth_and_returns_lineage(tmp_path):
    cfg = prepared_cfg(tmp_path, operator_token="secret-token")
    conversation_id = create_conversation("Protected source", cfg)["conversation_id"]
    run = submit(conversation_id, "Protected canonical question", cfg)
    client = TestClient(create_app(cfg))
    payload = {
        "prompt": "Authorized semantic replacement",
        "mode": "ask",
        "idempotency_key": "semantic-branch-0005",
        "options": {"model_provider": "mock", "retrieval_strategy": "persistent"},
    }

    denied = client.post(f"/conversations/{conversation_id}/runs/{run['run_id']}/branch", json=payload)
    assert denied.status_code == 401

    allowed = client.post(
        f"/conversations/{conversation_id}/runs/{run['run_id']}/branch",
        json=payload,
        headers={"X-Mucaro-Token": "secret-token"},
    )
    assert allowed.status_code == 200
    body = allowed.json()
    assert body["status"] == "RUN_BRANCH_CREATED"
    assert body["conversation"]["branch_origin"]["source_run_id"] == run["run_id"]
    assert body["run"]["lineage"]["source_run_id"] == run["run_id"]
