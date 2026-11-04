import base64
import copy
import hashlib
import json
import sqlite3

import pytest

from backend.app import agent_checkpoints as checkpoints
from backend.app import research_orchestration as research
from backend.tests.test_web_browsing import make_settings

SHA = "1" * 40


@pytest.fixture
def inputs(tmp_path):
    root = tmp_path / "evidence"
    root.mkdir()
    raw = "Río Grande is the location explicitly named in this synthetic fixture.".encode()
    digest = hashlib.sha256(raw).hexdigest()
    snapshot = {
        "schema_version": "mucaro_web_snapshot_v1",
        "snapshot_id": "s1",
        "requested_url": "https://example.org/fixture",
        "final_url": "https://example.org/fixture",
        "retrieval_utc": "2026-11-02T12:00:00Z",
        "http_status": 200,
        "content_base64": base64.b64encode(raw).decode(),
        "content_sha256": digest,
        "content_size_bytes": len(raw),
    }
    blob = json.dumps(snapshot).encode()
    (root / "snapshot.json").write_bytes(blob)
    packet = {
        "schema_version": "mucaro-research-packet/1",
        "sources": [{"source_id": "s1", "path": "snapshot.json", "snapshot_sha256": hashlib.sha256(blob).hexdigest()}],
        "claims": [
            {
                "claim_id": "c1",
                "text": "The fixture names Río Grande.",
                "source_id": "s1",
                "byte_start": 0,
                "byte_end": len(raw),
                "span_sha256": digest,
                "epistemic_class": "FACT",
            }
        ],
    }
    plan = {
        "schema_version": research.SCHEMA,
        "objective": "Synthetic pipeline check",
        "provider": "mock",
        "model": "mock-cited-synthesizer-v1",
        "allow_live": False,
        "max_model_calls": 2,
        "max_input_chars": 10000,
        "deadline_seconds": 3600,
        "questions": [
            {"id": "q2", "query": "Summarize the source-bounded location.", "depends_on": ["q1"], "claim_ids": ["c1"]},
            {"id": "q1", "query": "Which location is named?", "depends_on": [], "claim_ids": ["c1"]},
        ],
    }
    return root, packet, plan, make_settings(tmp_path / "runtime")


def start(inputs):
    root, packet, plan, cfg = inputs
    return research.create_research(plan, packet, root, source_sha=SHA, cfg=cfg)


def test_resume_uses_existing_checkpoints_and_router_without_replaying_success(inputs, monkeypatch):
    calls = []
    original = research.model_router.generate_answer

    def counted(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(research.model_router, "generate_answer", counted)
    checkpoint = start(inputs)
    cfg = inputs[3]
    for _ in range(6):
        checkpoint = research.advance_research(checkpoint["checkpoint_id"], source_sha=SHA, cfg=cfg)
    assert checkpoint["state"] == "COMPLETED"
    assert len(calls) == 2
    assert [s["id"] for s in checkpoint["completed_steps"]] == ["claims", "question:q1", "question:q2", "report"]
    report = checkpoint["completed_steps"][-1]["result"]["payload"]
    assert report["release_certification"] == "WITHHELD"
    assert report["claims"][0]["entailment"] == "OPEN"
    assert report["question_count"] == 2


def test_plan_cycles_duplicates_and_unknown_claims_rejected(inputs):
    _, packet, plan, _ = inputs
    for mutation in ("cycle", "duplicate", "missing_claim"):
        candidate = copy.deepcopy(plan)
        if mutation == "cycle":
            candidate["questions"][1]["depends_on"] = ["q2"]
        if mutation == "duplicate":
            candidate["questions"][1]["id"] = "q2"
        if mutation == "missing_claim":
            candidate["questions"][0]["claim_ids"] = ["absent"]
        with pytest.raises(ValueError):
            research.validate_plan(candidate, packet)


def test_model_budget_survives_restart(inputs):
    inputs[2]["max_model_calls"] = 1
    cp = start(inputs)
    result = research.advance_research(cp["checkpoint_id"], source_sha=SHA, cfg=inputs[3], max_steps=10)
    assert result["state"] == "PAUSED_RESOURCE"
    assert result["pause_reason"] == "RESEARCH_MODEL_CALL_BUDGET"
    checkpoints.resume_checkpoint(cp["checkpoint_id"], inputs[3])
    again = research.advance_research(cp["checkpoint_id"], source_sha=SHA, cfg=inputs[3], max_steps=10)
    assert again["state"] == "PAUSED_RESOURCE"
    assert len(again["completed_steps"]) == 2


def test_tampered_source_and_wrong_head_rejected(inputs):
    cp = start(inputs)
    with pytest.raises(ValueError, match="commit changed"):
        research.advance_research(cp["checkpoint_id"], source_sha="2" * 40, cfg=inputs[3])
    (inputs[0] / "snapshot.json").write_text("{}")
    with pytest.raises(ValueError, match="integrity"):
        research.advance_research(cp["checkpoint_id"], source_sha=SHA, cfg=inputs[3])


def test_contradictions_preserved_and_block_completion(inputs):
    second = dict(inputs[1]["claims"][0], claim_id="c2", contradicts=["c1"])
    inputs[1]["claims"].append(second)
    cp = start(inputs)
    result = research.advance_research(cp["checkpoint_id"], source_sha=SHA, cfg=inputs[3], max_steps=10)
    assert result["state"] == "BLOCKED_EXTERNAL"
    assert result["contradictions"][0]["state"] == "UNRESOLVED"
    assert result["completed_steps"][-1]["id"] == "report"


def test_uncertain_provider_failure_cannot_repeat_paid_call(inputs, monkeypatch):
    cp = start(inputs)
    research.advance_research(cp["checkpoint_id"], source_sha=SHA, cfg=inputs[3])

    def failed(*args, **kwargs):
        raise TimeoutError("synthetic timeout")

    monkeypatch.setattr(research.model_router, "generate_answer", failed)
    with pytest.raises(TimeoutError):
        research.advance_research(cp["checkpoint_id"], source_sha=SHA, cfg=inputs[3])
    stored = checkpoints.get_checkpoint(cp["checkpoint_id"], inputs[3])
    assert stored["state"] == "BLOCKED_EXTERNAL"
    assert stored["in_flight_step"]["step_id"] == "question:q1"
    checkpoints.resume_checkpoint(cp["checkpoint_id"], inputs[3])
    with pytest.raises(ValueError, match="in-flight"):
        research.advance_research(cp["checkpoint_id"], source_sha=SHA, cfg=inputs[3])


def test_hallucinated_citation_never_accepted(inputs, monkeypatch):
    cp = start(inputs)
    research.advance_research(cp["checkpoint_id"], source_sha=SHA, cfg=inputs[3])
    monkeypatch.setattr(
        research.model_router,
        "generate_answer",
        lambda *a, **k: {
            "model_provider": "mock",
            "model_name": inputs[2]["model"],
            "answer": "unsupported",
            "cited_chunk_ids": ["invented"],
        },
    )
    with pytest.raises(ValueError, match="cites"):
        research.advance_research(cp["checkpoint_id"], source_sha=SHA, cfg=inputs[3])


def test_provider_blocked_without_implicit_mock_fallback(inputs, monkeypatch):
    cp = start(inputs)
    research.advance_research(cp["checkpoint_id"], source_sha=SHA, cfg=inputs[3])
    monkeypatch.setattr(
        research.model_routing_policy, "plan_model_route", lambda **kw: {"status": "MODEL_ROUTE_BLOCKED"}
    )
    result = research.advance_research(cp["checkpoint_id"], source_sha=SHA, cfg=inputs[3])
    assert result["state"] == "PAUSED_PROVIDER"
    assert not result.get("in_flight_step")


def test_database_rollback_rehearsal_preserves_frozen_inputs(inputs, tmp_path):
    cp = start(inputs)
    cfg = inputs[3]
    backup = tmp_path / "before.sqlite3"
    with sqlite3.connect(cfg.sqlite_path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    done = research.advance_research(cp["checkpoint_id"], source_sha=SHA, cfg=cfg, max_steps=10)
    restored_cfg = make_settings(tmp_path / "restored")
    restored_cfg.ensure_dirs()
    with sqlite3.connect(backup) as source, sqlite3.connect(restored_cfg.sqlite_path) as target:
        source.backup(target)
    restored = checkpoints.get_checkpoint(cp["checkpoint_id"], restored_cfg)
    assert restored["completed_steps"] == []
    assert restored["metadata"] == cp["metadata"]
    replay = research.advance_research(cp["checkpoint_id"], source_sha=SHA, cfg=restored_cfg, max_steps=10)
    assert replay["state"] == "COMPLETED"
    assert replay["completed_steps"][-1]["result"] == done["completed_steps"][-1]["result"]


def test_stored_output_tamper_rejected(inputs):
    cp = start(inputs)
    current = research.advance_research(cp["checkpoint_id"], source_sha=SHA, cfg=inputs[3])
    current["completed_steps"][0]["result"]["payload"]["claims"][0]["text"] = "rewritten"
    with sqlite3.connect(inputs[3].sqlite_path) as connection:
        connection.execute(
            "UPDATE agent_checkpoints SET payload_json=? WHERE checkpoint_id=?",
            (json.dumps(current), cp["checkpoint_id"]),
        )
    with pytest.raises(ValueError, match="output hash"):
        research.advance_research(cp["checkpoint_id"], source_sha=SHA, cfg=inputs[3])
