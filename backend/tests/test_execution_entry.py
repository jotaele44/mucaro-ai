from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.app import execution_entry, work_runs
from backend.app.conversations import create_conversation
from backend.app.db import get_connection
from backend.app.execution_entry import (
    ExecutionEntryError,
    _canonical_hash,
    create_entry_artifact,
    current_conversation_entry_context,
    decide_entry,
    entry_labels,
    get_entry_artifact,
    get_entry_decision,
    resume_interrupted_entry_decision,
    resume_pending_entry_decisions,
    transition_entry_artifact,
)
from backend.app.tools import list_tool_calls
from backend.app.work_runs import get_run, list_runs
from backend.tests.test_api import make_settings

SHA = "a" * 40
OTHER_SHA = "b" * 40
def _gate(gate_id: str, state: str) -> dict[str, str]:
    evidence = f"fixture://execution-entry/{gate_id}/{state.lower()}"
    return {
        "gate_id": gate_id,
        "state": state,
        "evidence_ref": evidence,
        "evidence_sha256": hashlib.sha256(evidence.encode("utf-8")).hexdigest(),
    }


PASS_GATES = [
    _gate("scope", "PASS"),
    _gate("authorization", "PASS"),
    _gate("dependencies", "PASS"),
]


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
    return create_conversation("execution entry", cfg)["conversation_id"]


def _artifact(
    cfg,
    conversation,
    *,
    mode="GOAL",
    gates=None,
    runtime_mode="plan",
    prompt="Summarize the approved objective.",
    actors=None,
):
    return create_entry_artifact(
        mode=mode,
        objective_id="objective-1",
        conversation_id=conversation,
        prompt=prompt,
        runtime_mode=runtime_mode,
        options={"model_provider": "mock"},
        source_commit=SHA,
        required_gates=list(gates or PASS_GATES),
        actor="user",
        authorized_actors=list(actors or ["user"]),
        cfg=cfg,
    )


def _ready(cfg, artifact):
    target = "VECTOR_READY" if artifact["mode"] == "GOAL" else "PLAN_APPROVED"
    return transition_entry_artifact(
        artifact["artifact_id"],
        artifact["revision"],
        expected_version=artifact["version"],
        target_state=target,
        actor="user",
        cfg=cfg,
    )


def _decide(cfg, artifact, action, key, **kwargs):
    return decide_entry(
        artifact["artifact_id"],
        artifact["revision"],
        action=action,
        expected_version=artifact["version"],
        actor=kwargs.pop("actor", "user"),
        idempotency_key=key,
        source_commit=kwargs.pop("source_commit", SHA),
        changes=kwargs.pop("changes", None),
        cfg=cfg,
        **kwargs,
    )


def _run_count(cfg, conversation):
    return len(list_runs(conversation_id=conversation, limit=100, cfg=cfg)["runs"])


def _tamper_artifact(cfg, artifact, **changes):
    with get_connection(cfg.sqlite_path) as conn:
        row = conn.execute(
            "SELECT payload_json FROM execution_entry_artifacts WHERE artifact_id = ? AND revision = ?",
            (artifact["artifact_id"], artifact["revision"]),
        ).fetchone()
        payload = json.loads(row["payload_json"])
        payload.update(changes)
        payload["artifact_sha256"] = _canonical_hash({k: v for k, v in payload.items() if k != "artifact_sha256"})
        conn.execute(
            "UPDATE execution_entry_artifacts SET payload_json = ? WHERE artifact_id = ? AND revision = ?",
            (json.dumps(payload, sort_keys=True), artifact["artifact_id"], artifact["revision"]),
        )


# P01
def test_p01_goal_ready_proceed_dispatches_exact_revision(cfg, conversation):
    ready = _ready(cfg, _artifact(cfg, conversation))
    decision = _decide(cfg, ready, "Proceed", "p01-proceed")
    assert decision["state"] == "DISPATCHED"
    assert decision["artifact_sha256"] == ready["artifact_sha256"]
    run = get_run(decision["run_id"], cfg)
    assert run["options"]["execution_entry"]["revision"] == 1
    assert run["options"]["execution_entry"]["artifact_id"] == ready["artifact_id"]
    assert run["options"]["execution_entry"]["artifact_sha256"] == ready["artifact_sha256"]


# P02
def test_p02_goal_adjust_creates_revision_without_execution(cfg, conversation):
    original = _artifact(cfg, conversation)
    other_conversation = create_conversation("execution entry other", cfg)["conversation_id"]
    second = _artifact(cfg, other_conversation)
    assert second["artifact_id"] != original["artifact_id"]
    decision = _decide(cfg, original, "Adjust", "p02-adjust", changes={"prompt": "Adjusted objective."})
    revised = get_entry_artifact(original["artifact_id"], 2, cfg)
    assert decision["state"] == "REVISION_CREATED"
    assert revised["state"] == "VECTOR_CANDIDATE"
    assert revised["predecessor_revision"] == 1
    assert _run_count(cfg, conversation) == 0


# P03
def test_p03_plan_approved_execute_dispatches_exact_revision(cfg, conversation):
    ready = _ready(cfg, _artifact(cfg, conversation, mode="PLAN"))
    decision = _decide(cfg, ready, "Execute", "p03-execute")
    run = get_run(decision["run_id"], cfg)
    assert decision["state"] == "DISPATCHED"
    assert run["options"]["execution_entry"]["mode"] == "PLAN"
    assert run["options"]["execution_entry"]["revision"] == 1


# P04
def test_p04_plan_revise_creates_candidate_without_execution(cfg, conversation):
    original = _artifact(cfg, conversation, mode="PLAN")
    decision = _decide(cfg, original, "Revise", "p04-revise", changes={"prompt": "Revised plan."})
    revised = get_entry_artifact(original["artifact_id"], 2, cfg)
    assert decision["state"] == "REVISION_CREATED"
    assert revised["state"] == "PLAN_CANDIDATE"
    assert _run_count(cfg, conversation) == 0


# P05
def test_p05_same_key_same_request_returns_original_decision(cfg, conversation):
    ready = _ready(cfg, _artifact(cfg, conversation))
    first = _decide(cfg, ready, "Proceed", "p05-replay")
    second = _decide(cfg, ready, "Proceed", "p05-replay")
    assert second["decision_id"] == first["decision_id"]
    assert second["run_id"] == first["run_id"]
    assert _run_count(cfg, conversation) == 1


# P06
def test_p06_interrupted_run_resume_preserves_entry_revision(cfg, conversation, monkeypatch):
    ready = _ready(cfg, _artifact(cfg, conversation))
    first = _decide(cfg, ready, "Proceed", "p06-first")
    old_run = get_run(first["run_id"], cfg)
    with get_connection(cfg.sqlite_path) as conn:
        conn.execute("UPDATE work_runs SET state = 'INTERRUPTED' WHERE run_id = ?", (old_run["run_id"],))
    resumed = resume_interrupted_entry_decision(
        first["decision_id"], actor="user", idempotency_key="p06-resume", cfg=cfg
    )
    replayed = resume_interrupted_entry_decision(
        first["decision_id"], actor="user", idempotency_key="p06-resume", cfg=cfg
    )
    new_run = get_run(resumed["run_id"], cfg)
    assert replayed["run_id"] == resumed["run_id"]
    assert resumed["prior_run_ids"] == [old_run["run_id"]]
    assert new_run["options"]["execution_entry"]["revision"] == 1
    assert new_run["options"]["execution_entry"]["artifact_sha256"] == ready["artifact_sha256"]
    assert new_run["options"]["execution_entry"]["resume_of_run_id"] == old_run["run_id"]

    # A separate interrupted decision must remain exactly-once even when two
    # different client idempotency keys race before the decision receipt update.
    race_ready = _ready(cfg, _artifact(cfg, conversation))
    race_first = _decide(cfg, race_ready, "Proceed", "p06-race-first")
    race_old_run = get_run(race_first["run_id"], cfg)
    with get_connection(cfg.sqlite_path) as conn:
        conn.execute("UPDATE work_runs SET state = 'INTERRUPTED' WHERE run_id = ?", (race_old_run["run_id"],))

    actual_submit = execution_entry.submit_message
    barrier = threading.Barrier(2)

    def synchronized_submit(*args, **kwargs):
        barrier.wait(timeout=5)
        return actual_submit(*args, **kwargs)

    monkeypatch.setattr(execution_entry, "submit_message", synchronized_submit)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                resume_interrupted_entry_decision,
                race_first["decision_id"],
                actor="user",
                idempotency_key=key,
                cfg=cfg,
            )
            for key in ("p06-race-a", "p06-race-b")
        ]
        raced = [future.result(timeout=10) for future in futures]

    assert raced[0]["run_id"] == raced[1]["run_id"]
    persisted = get_entry_decision(race_first["decision_id"], cfg)
    assert persisted["resume_count"] == 1
    assert persisted["prior_run_ids"] == [race_old_run["run_id"]]


# P07
def test_p07_completed_stage_keeps_verifiable_artifact_and_decision_receipts(cfg, conversation):
    ready = _ready(cfg, _artifact(cfg, conversation))
    decision = _decide(cfg, ready, "Proceed", "p07-receipts")
    assert get_run(decision["run_id"], cfg)["state"] == "COMPLETED"
    artifact_receipt = get_entry_artifact(ready["artifact_id"], 1, cfg)
    decision_receipt = get_entry_decision(decision["decision_id"], cfg)
    assert len(artifact_receipt["artifact_sha256"]) == 64
    assert decision_receipt["artifact_sha256"] == artifact_receipt["artifact_sha256"]
    assert len(decision_receipt["receipt_sha256"]) == 64
    next_entry = _artifact(cfg, conversation, mode="PLAN", prompt="Prepare the next governed plan.")
    assert next_entry["mode"] == "PLAN"
    assert next_entry["state"] == "PLAN_CANDIDATE"


# P08
def test_p08_localized_labels_preserve_action_semantics():
    cases = [
        ("en", "GOAL", "Proceed", "Adjust"),
        ("en-US", "PLAN", "Execute", "Revise"),
        ("es", "GOAL", "Proceder", "Ajustar"),
        ("es-PR", "PLAN", "Ejecutar", "Revisar"),
    ]
    for locale, mode, primary, secondary in cases:
        assert entry_labels(locale, mode) == {
            "locale": "es" if locale.startswith("es") else "en",
            "mode": mode,
            "primary": primary,
            "secondary": secondary,
        }


# N01
def test_n01_stale_vector_revision_is_rejected(cfg, conversation):
    original = _artifact(cfg, conversation)
    with pytest.raises(ExecutionEntryError) as duplicate_exc:
        _artifact(cfg, conversation)
    assert duplicate_exc.value.status == "ERROR_EXECUTION_ENTRY_EXISTING_ARTIFACT"
    _decide(cfg, original, "Adjust", "n01-adjust", changes={"prompt": "next"})
    with pytest.raises(ExecutionEntryError) as approval_exc:
        transition_entry_artifact(
            original["artifact_id"], 1, expected_version=1, target_state="VECTOR_READY", actor="user", cfg=cfg
        )
    assert approval_exc.value.status == "ERROR_EXECUTION_ENTRY_STALE_REVISION"
    with pytest.raises(ExecutionEntryError) as exc:
        _decide(cfg, original, "Proceed", "n01-stale")
    assert exc.value.status == "ERROR_EXECUTION_ENTRY_STALE_REVISION"


# N02
def test_n02_stale_plan_revision_is_rejected(cfg, conversation):
    original = _artifact(cfg, conversation, mode="PLAN")
    _decide(cfg, original, "Revise", "n02-revise", changes={"prompt": "next"})
    with pytest.raises(ExecutionEntryError) as exc:
        _decide(cfg, original, "Execute", "n02-stale")
    assert exc.value.status == "ERROR_EXECUTION_ENTRY_STALE_REVISION"


# N03
def test_n03_missing_vector_identity_is_rejected(cfg, conversation):
    artifact = _artifact(cfg, conversation)
    _tamper_artifact(cfg, artifact, vector_id=None)
    with pytest.raises(ExecutionEntryError) as exc:
        get_entry_artifact(artifact["artifact_id"], 1, cfg)
    assert exc.value.status == "ERROR_EXECUTION_ENTRY_IDENTITY"


# N04
def test_n04_missing_plan_identity_is_rejected(cfg, conversation):
    artifact = _artifact(cfg, conversation, mode="PLAN")
    _tamper_artifact(cfg, artifact, plan_id=None)
    with pytest.raises(ExecutionEntryError) as exc:
        get_entry_artifact(artifact["artifact_id"], 1, cfg)
    assert exc.value.status == "ERROR_EXECUTION_ENTRY_IDENTITY"


# N05
def test_n05_execute_without_approval_is_rejected(cfg, conversation):
    artifact = _artifact(cfg, conversation, mode="PLAN")
    with pytest.raises(ExecutionEntryError) as exc:
        _decide(cfg, artifact, "Execute", "n05-no-approval")
    assert exc.value.status == "ERROR_EXECUTION_ENTRY_NOT_READY"


# N06
def test_n06_failed_dependency_gate_blocks_approval(cfg, conversation):
    artifact = _artifact(cfg, conversation, gates=[_gate("dependency", "FAIL")])
    with pytest.raises(ExecutionEntryError) as exc:
        _ready(cfg, artifact)
    assert exc.value.status == "ERROR_EXECUTION_ENTRY_NOT_READY"


# N07
def test_n07_unresolved_risk_gate_blocks_approval(cfg, conversation):
    artifact = _artifact(cfg, conversation, gates=[_gate("risk", "UNRESOLVED")])
    with pytest.raises(ExecutionEntryError) as exc:
        _ready(cfg, artifact)
    assert exc.value.status == "ERROR_EXECUTION_ENTRY_NOT_READY"

    missing_evidence = {"gate_id": "authorization", "state": "PASS"}
    with pytest.raises(ExecutionEntryError) as evidence_exc:
        _artifact(
            cfg,
            create_conversation("entry missing evidence", cfg)["conversation_id"],
            gates=[missing_evidence],
        )
    assert evidence_exc.value.status == "ERROR_EXECUTION_ENTRY_GATES"

    legacy = _artifact(
        cfg,
        create_conversation("entry legacy missing evidence", cfg)["conversation_id"],
    )
    _tamper_artifact(
        cfg,
        legacy,
        required_gates=[{"gate_id": "authorization", "state": "PASS"}],
    )
    with pytest.raises(ExecutionEntryError) as legacy_exc:
        _ready(cfg, get_entry_artifact(legacy["artifact_id"], 1, cfg))
    assert legacy_exc.value.status == "ERROR_EXECUTION_ENTRY_GATES"


# N08
def test_n08_unauthorized_actor_cannot_approve_or_decide(cfg, conversation):
    artifact = _artifact(cfg, conversation, actors=["user"])
    with pytest.raises(ExecutionEntryError) as exc:
        transition_entry_artifact(
            artifact["artifact_id"], 1, expected_version=1, target_state="VECTOR_READY",
            actor="intruder", cfg=cfg
        )
    assert exc.value.status == "ERROR_EXECUTION_ENTRY_UNAUTHORIZED_ACTOR"


# N09
def test_n09_entry_does_not_bypass_tool_specific_approval(cfg, conversation):
    ready = _ready(cfg, _artifact(cfg, conversation, runtime_mode="ask", prompt="/fetch https://example.com"))
    decision = _decide(cfg, ready, "Proceed", "n09-tool-gate")
    run = get_run(decision["run_id"], cfg)
    assert run["state"] == "AWAITING_APPROVAL"
    calls = [row for row in list_tool_calls(cfg)["tool_calls"] if row.get("run_id") == run["run_id"]]
    assert len(calls) == 1
    assert calls[0]["status"] == "PENDING_APPROVAL"


# N10
def test_n10_same_key_different_payload_fails(cfg, conversation):
    artifact = _artifact(cfg, conversation)
    _decide(cfg, artifact, "Adjust", "n10-conflict", changes={"prompt": "one"})
    with pytest.raises(ExecutionEntryError) as exc:
        _decide(cfg, artifact, "Adjust", "n10-conflict", changes={"prompt": "two"})
    assert exc.value.status == "ERROR_EXECUTION_ENTRY_IDEMPOTENCY_CONFLICT"


# N11
def test_n11_competing_claim_on_same_revision_is_rejected(cfg, conversation):
    ready = _ready(cfg, _artifact(cfg, conversation))
    _decide(cfg, ready, "Proceed", "n11-first")
    with pytest.raises(ExecutionEntryError) as exc:
        _decide(cfg, ready, "Proceed", "n11-second")
    assert exc.value.status == "ERROR_EXECUTION_ENTRY_ALREADY_CLAIMED"


# N12
def test_n12_revise_never_modifies_approved_plan_in_place(cfg, conversation):
    approved = _ready(cfg, _artifact(cfg, conversation, mode="PLAN"))
    _decide(cfg, approved, "Revise", "n12-revise", changes={"prompt": "new revision"})
    original = get_entry_artifact(approved["artifact_id"], 1, cfg)
    revised = get_entry_artifact(approved["artifact_id"], 2, cfg)
    assert original["state"] == "PLAN_APPROVED"
    assert original["prompt"] != revised["prompt"]
    assert revised["state"] == "PLAN_CANDIDATE"


# N13
def test_n13_running_decision_never_silently_migrates_to_new_revision(cfg, conversation):
    approved = _ready(cfg, _artifact(cfg, conversation, mode="PLAN"))
    executed = _decide(cfg, approved, "Execute", "n13-execute")
    with get_connection(cfg.sqlite_path) as conn:
        conn.execute("UPDATE work_runs SET state = 'RUNNING' WHERE run_id = ?", (executed["run_id"],))

    revised_decision = _decide(cfg, approved, "Revise", "n13-revise", changes={"prompt": "future revision"})
    assert get_entry_decision(executed["decision_id"], cfg)["revision"] == 1
    assert revised_decision["new_revision"] == 2
    assert get_entry_artifact(approved["artifact_id"], 2, cfg)["state"] == "PLAN_CANDIDATE"

    active_context = current_conversation_entry_context(conversation, cfg)
    assert active_context["entry"]["revision"] == 1
    assert active_context["decision"]["decision_id"] == executed["decision_id"]
    assert active_context["run"]["run_id"] == executed["run_id"]
    assert active_context["run"]["state"] == "RUNNING"
    assert active_context["latest_entry"]["revision"] == 2

    with get_connection(cfg.sqlite_path) as conn:
        conn.execute("UPDATE work_runs SET state = 'COMPLETED' WHERE run_id = ?", (executed["run_id"],))
    settled_context = current_conversation_entry_context(conversation, cfg)
    assert settled_context["entry"]["revision"] == 2
    assert settled_context["latest_entry"]["revision"] == 2


# N14
def test_n14_adjusted_next_vector_is_not_automatically_authorized(cfg, conversation):
    original = _artifact(cfg, conversation)
    _decide(cfg, original, "Adjust", "n14-adjust", changes={"prompt": "next vector"})
    revised = get_entry_artifact(original["artifact_id"], 2, cfg)
    assert revised["state"] == "VECTOR_CANDIDATE"
    with pytest.raises(ExecutionEntryError) as exc:
        _decide(cfg, revised, "Proceed", "n14-no-auto")
    assert exc.value.status == "ERROR_EXECUTION_ENTRY_NOT_READY"


# N15
def test_n15_double_activation_replay_cannot_create_second_run(cfg, conversation):
    ready = _ready(cfg, _artifact(cfg, conversation))
    first = _decide(cfg, ready, "Proceed", "n15-double")
    second = _decide(cfg, ready, "Proceed", "n15-double")
    assert first["decision_id"] == second["decision_id"]
    assert _run_count(cfg, conversation) == 1


# N16
def test_n16_restart_between_claim_and_dispatch_recovers_exactly_once(cfg, conversation, monkeypatch):
    ready = _ready(cfg, _artifact(cfg, conversation))
    actual_dispatch = execution_entry.dispatch_entry_decision
    monkeypatch.setattr(
        execution_entry,
        "dispatch_entry_decision",
        lambda decision_id, cfg=cfg: get_entry_decision(decision_id, cfg),
    )
    claimed = _decide(cfg, ready, "Proceed", "n16-claim")
    assert claimed["state"] == "CLAIMED"
    assert _run_count(cfg, conversation) == 0
    monkeypatch.setattr(execution_entry, "dispatch_entry_decision", actual_dispatch)
    recovery = resume_pending_entry_decisions(cfg)
    assert recovery["dispatched"] == [claimed["decision_id"]]
    assert _run_count(cfg, conversation) == 1
    assert resume_pending_entry_decisions(cfg)["dispatched"] == []


# N17
def test_n17_corrupted_decision_receipt_hash_is_rejected(cfg, conversation):
    artifact = _artifact(cfg, conversation)
    decision = _decide(cfg, artifact, "Adjust", "n17-receipt", changes={"prompt": "revision"})
    with get_connection(cfg.sqlite_path) as conn:
        row = conn.execute(
            "SELECT payload_json FROM execution_entry_decisions WHERE decision_id = ?",
            (decision["decision_id"],),
        ).fetchone()
        payload = json.loads(row["payload_json"])
        payload["actor"] = "tampered"
        conn.execute(
            "UPDATE execution_entry_decisions SET payload_json = ? WHERE decision_id = ?",
            (json.dumps(payload, sort_keys=True), decision["decision_id"]),
        )
    with pytest.raises(ExecutionEntryError) as exc:
        get_entry_decision(decision["decision_id"], cfg)
    assert exc.value.status == "ERROR_EXECUTION_ENTRY_RECEIPT_CORRUPT"


# N18
def test_n18_changed_source_commit_is_rejected(cfg, conversation, monkeypatch):
    ready = _ready(cfg, _artifact(cfg, conversation))
    with pytest.raises(ExecutionEntryError) as exc:
        _decide(cfg, ready, "Proceed", "n18-source", source_commit=OTHER_SHA)
    assert exc.value.status == "ERROR_EXECUTION_ENTRY_SOURCE_DRIFT"

    candidate = _artifact(cfg, create_conversation("execution entry source drift", cfg)["conversation_id"])
    with pytest.raises(ExecutionEntryError) as revision_exc:
        _decide(
            cfg,
            candidate,
            "Adjust",
            "n18-revision-source",
            changes={"source_commit": OTHER_SHA, "prompt": "must not persist false source identity"},
        )
    assert revision_exc.value.status == "ERROR_EXECUTION_ENTRY_SOURCE_DRIFT"
    with pytest.raises(ExecutionEntryError):
        get_entry_artifact(candidate["artifact_id"], 2, cfg)

    actual_dispatch = execution_entry.dispatch_entry_decision
    monkeypatch.setattr(
        execution_entry,
        "dispatch_entry_decision",
        lambda decision_id, cfg=cfg: get_entry_decision(decision_id, cfg),
    )
    claimed = _decide(cfg, ready, "Proceed", "n18-claimed")
    assert claimed["state"] == "CLAIMED"
    monkeypatch.setattr(execution_entry, "dispatch_entry_decision", actual_dispatch)
    monkeypatch.setenv("MUCARO_SOURCE_HEAD", OTHER_SHA)
    with pytest.raises(ExecutionEntryError) as recovery_exc:
        resume_pending_entry_decisions(cfg)
    assert recovery_exc.value.status == "ERROR_EXECUTION_ENTRY_SOURCE_DRIFT"
    assert _run_count(cfg, conversation) == 0


# N19
def test_n19_not_ready_activation_is_rejected_even_if_client_ignores_disabled_ui(cfg, conversation):
    candidate = _artifact(cfg, conversation)
    with pytest.raises(ExecutionEntryError) as exc:
        _decide(cfg, candidate, "Proceed", "n19-disabled-bypass")
    assert exc.value.status == "ERROR_EXECUTION_ENTRY_NOT_READY"
    assert _run_count(cfg, conversation) == 0


# N20
def test_n20_locale_switch_is_side_effect_free(cfg, conversation):
    artifact = _artifact(cfg, conversation)
    before = _run_count(cfg, conversation)
    english = entry_labels("en", artifact["mode"])
    spanish = entry_labels("es-PR", artifact["mode"])
    assert english["primary"] == "Proceed"
    assert spanish["primary"] == "Proceder"
    assert spanish["secondary"] == "Ajustar"
    assert _run_count(cfg, conversation) == before == 0
