"""Startup recovery of execution-entry decisions that are stranded in CLAIMED.

A decision is stranded when the process dies between claim and dispatch. Recovery
at startup must never stop the server from booting: when the runtime source commit
changed since the claim, the decision stays CLAIMED (no run is created) and is
resumed on a later start whose source identity matches the claimed artifact.

`resume_pending_entry_decisions` itself still raises on drift (pinned by N18 in
test_execution_entry.py); these tests pin the `create_app` call-site guard.
"""

from __future__ import annotations

import hashlib
import json
import logging

import pytest
from fastapi.testclient import TestClient

from backend.app import execution_entry, work_runs
from backend.app.conversations import create_conversation
from backend.app.db import get_connection
from backend.app.execution_entry import (
    create_entry_artifact,
    decide_entry,
    get_entry_decision,
    transition_entry_artifact,
)
from backend.app.main import create_app
from backend.app.work_runs import list_runs
from backend.tests.test_api import make_settings

SHA = "a" * 40
OTHER_SHA = "b" * 40
MAIN_LOGGER = "backend.app.main"


def _gate(gate_id: str) -> dict[str, str]:
    evidence = f"fixture://execution-entry-startup/{gate_id}"
    return {
        "gate_id": gate_id,
        "state": "PASS",
        "evidence_ref": evidence,
        "evidence_sha256": hashlib.sha256(evidence.encode("utf-8")).hexdigest(),
    }


PASS_GATES = [_gate("scope"), _gate("authorization"), _gate("dependencies")]


@pytest.fixture(autouse=True)
def inline_runs():
    work_runs.set_execution_mode("inline")
    yield
    work_runs.set_execution_mode("thread")


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCARO_SOURCE_HEAD", SHA)
    settings = make_settings(tmp_path)
    settings.ensure_dirs()
    return settings


@pytest.fixture()
def conversation(cfg):
    return create_conversation("execution entry startup", cfg)["conversation_id"]


@pytest.fixture()
def claimed(cfg, conversation, monkeypatch):
    """A GOAL decision stranded in CLAIMED: the process died after claim, before dispatch."""
    artifact = create_entry_artifact(
        mode="GOAL",
        objective_id="startup-recovery",
        conversation_id=conversation,
        prompt="Summarize the approved objective.",
        runtime_mode="plan",
        options={"model_provider": "mock"},
        source_commit=SHA,
        required_gates=list(PASS_GATES),
        actor="user",
        authorized_actors=["user"],
        cfg=cfg,
    )
    ready = transition_entry_artifact(
        artifact["artifact_id"],
        artifact["revision"],
        expected_version=artifact["version"],
        target_state="VECTOR_READY",
        actor="user",
        cfg=cfg,
    )
    with monkeypatch.context() as patch:
        patch.setattr(
            execution_entry,
            "dispatch_entry_decision",
            lambda decision_id, cfg=cfg: get_entry_decision(decision_id, cfg),
        )
        decision = decide_entry(
            ready["artifact_id"],
            ready["revision"],
            action="Proceed",
            expected_version=ready["version"],
            actor="user",
            idempotency_key="startup-claim",
            source_commit=SHA,
            changes=None,
            cfg=cfg,
        )
    assert decision["state"] == "CLAIMED"
    assert _run_count(cfg, conversation) == 0
    return decision


def _run_count(cfg, conversation) -> int:
    return len(list_runs(conversation_id=conversation, limit=100, cfg=cfg)["runs"])


def _stored_state(cfg, decision_id: str) -> str:
    with get_connection(cfg.sqlite_path) as conn:
        row = conn.execute(
            "SELECT state FROM execution_entry_decisions WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()
    return str(row["state"])


def _recovery_records(caplog) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == MAIN_LOGGER]


def test_changed_source_commit_does_not_block_startup_and_keeps_claim(cfg, conversation, claimed, monkeypatch, caplog):
    monkeypatch.setenv("MUCARO_SOURCE_HEAD", OTHER_SHA)

    with caplog.at_level(logging.WARNING, logger=MAIN_LOGGER):
        app = create_app(cfg)

    assert TestClient(app).get("/health").status_code == 200
    stranded = get_entry_decision(claimed["decision_id"], cfg)
    assert stranded["state"] == "CLAIMED"
    assert stranded["run_id"] is None
    assert _run_count(cfg, conversation) == 0

    records = _recovery_records(caplog)
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    message = records[0].getMessage()
    assert "ERROR_EXECUTION_ENTRY_SOURCE_DRIFT" in message
    assert f"expected={SHA} observed={OTHER_SHA}" in message
    assert claimed["decision_id"] in message


def test_claim_is_resumed_exactly_once_by_a_later_start_whose_source_matches(cfg, conversation, claimed, monkeypatch):
    monkeypatch.setenv("MUCARO_SOURCE_HEAD", OTHER_SHA)
    create_app(cfg)
    assert get_entry_decision(claimed["decision_id"], cfg)["state"] == "CLAIMED"
    assert _run_count(cfg, conversation) == 0

    monkeypatch.setenv("MUCARO_SOURCE_HEAD", SHA)
    create_app(cfg)
    recovered = get_entry_decision(claimed["decision_id"], cfg)
    assert recovered["state"] == "DISPATCHED"
    assert recovered["run_id"]
    assert _run_count(cfg, conversation) == 1

    create_app(cfg)
    assert _run_count(cfg, conversation) == 1


def test_same_source_commit_still_recovers_and_dispatches_claim(cfg, conversation, claimed, caplog):
    with caplog.at_level(logging.WARNING, logger=MAIN_LOGGER):
        create_app(cfg)

    recovered = get_entry_decision(claimed["decision_id"], cfg)
    assert recovered["state"] == "DISPATCHED"
    assert recovered["run_id"]
    assert _run_count(cfg, conversation) == 1
    assert _recovery_records(caplog) == []


def test_unavailable_source_identity_does_not_block_startup(cfg, conversation, claimed, monkeypatch, caplog):
    monkeypatch.setattr(execution_entry, "current_git_head", lambda project_root: None)

    with caplog.at_level(logging.WARNING, logger=MAIN_LOGGER):
        create_app(cfg)

    assert get_entry_decision(claimed["decision_id"], cfg)["state"] == "CLAIMED"
    assert _run_count(cfg, conversation) == 0
    records = _recovery_records(caplog)
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert "ERROR_EXECUTION_ENTRY_SOURCE_IDENTITY_UNAVAILABLE" in records[0].getMessage()


def test_integrity_failure_during_recovery_boots_but_is_logged_as_error(cfg, conversation, claimed, caplog):
    with get_connection(cfg.sqlite_path) as conn:
        row = conn.execute(
            "SELECT payload_json FROM execution_entry_decisions WHERE decision_id = ?",
            (claimed["decision_id"],),
        ).fetchone()
        payload = json.loads(row["payload_json"])
        payload["actor"] = "tampered"
        conn.execute(
            "UPDATE execution_entry_decisions SET payload_json = ? WHERE decision_id = ?",
            (json.dumps(payload, sort_keys=True), claimed["decision_id"]),
        )

    with caplog.at_level(logging.WARNING, logger=MAIN_LOGGER):
        create_app(cfg)

    assert _stored_state(cfg, claimed["decision_id"]) == "CLAIMED"
    assert _run_count(cfg, conversation) == 0
    records = _recovery_records(caplog)
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    message = records[0].getMessage()
    assert "ERROR_EXECUTION_ENTRY_RECEIPT_CORRUPT" in message
    assert claimed["decision_id"] in message
