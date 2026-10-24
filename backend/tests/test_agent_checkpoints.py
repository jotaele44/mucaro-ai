from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.agent_checkpoints import CheckpointError, create_checkpoint, get_checkpoint, resume_checkpoint, transition_checkpoint
from backend.app.config import Settings


SHA = "a" * 40


def make_settings(root: Path) -> Settings:
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


def test_checkpoint_persists_and_resumes_after_new_read(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    created = create_checkpoint(session_id="session-1", run_id="run-1", objective="Audit repositories", source_sha=SHA, state="EXECUTING", pending_steps=[{"id": "next"}], cfg=cfg)
    paused = transition_checkpoint(created["checkpoint_id"], state="PAUSED_PROVIDER", pause_reason="provider quota exhausted", cfg=cfg)

    reloaded = get_checkpoint(created["checkpoint_id"], cfg)
    assert reloaded == paused
    resumed = resume_checkpoint(created["checkpoint_id"], cfg)
    assert resumed["state"] == "EXECUTING"
    assert resumed["sequence"] == 2
    assert resumed["pending_steps"] == [{"id": "next"}]
    assert resumed["pause_reason"] is None


def test_blocked_and_paused_states_require_reason(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    created = create_checkpoint(session_id="session-1", run_id="run-1", objective="Audit", source_sha=SHA, cfg=cfg)
    with pytest.raises(CheckpointError) as exc:
        transition_checkpoint(created["checkpoint_id"], state="BLOCKED_EXTERNAL", cfg=cfg)
    assert exc.value.status == "ERROR_CHECKPOINT_PAUSE_REASON"


def test_terminal_checkpoint_fails_closed(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    created = create_checkpoint(session_id="session-1", run_id="run-1", objective="Audit", source_sha=SHA, state="EXECUTING", cfg=cfg)
    done = transition_checkpoint(created["checkpoint_id"], state="COMPLETED", cfg=cfg)
    assert done["state"] == "COMPLETED"
    with pytest.raises(CheckpointError) as exc:
        transition_checkpoint(created["checkpoint_id"], state="EXECUTING", cfg=cfg)
    assert exc.value.status == "ERROR_CHECKPOINT_TERMINAL"


def test_checkpoint_rejects_noncanonical_source_sha(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    with pytest.raises(CheckpointError) as exc:
        create_checkpoint(session_id="session-1", run_id="run-1", objective="Audit", source_sha="main", cfg=cfg)
    assert exc.value.status == "ERROR_CHECKPOINT_SOURCE_SHA"


def _executing(cfg: Settings, **kw):
    return create_checkpoint(session_id="s", run_id="r", objective="Audit", source_sha=SHA, state="EXECUTING", cfg=cfg, **kw)


def test_planning_cannot_jump_straight_to_completed(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    created = create_checkpoint(session_id="s", run_id="r", objective="Audit", source_sha=SHA, cfg=cfg)
    with pytest.raises(CheckpointError) as exc:
        transition_checkpoint(created["checkpoint_id"], state="COMPLETED", cfg=cfg)
    assert exc.value.status == "ERROR_CHECKPOINT_TRANSITION"


def test_every_disallowed_edge_is_rejected_and_every_allowed_edge_is_accepted(tmp_path: Path) -> None:
    from backend.app.agent_checkpoints import CHECKPOINT_STATES, TRANSITIONS

    cfg = make_settings(tmp_path)
    for source in sorted(CHECKPOINT_STATES - {"FAILED", "COMPLETED"}):
        for target in sorted(CHECKPOINT_STATES):
            checkpoint = create_checkpoint(session_id="s", run_id=f"r-{source}-{target}", objective="Audit", source_sha=SHA, cfg=cfg)
            if source != "PLANNING":
                # walk to the source state through legal edges only
                path = {"READY_FOR_APPROVAL": ["READY_FOR_APPROVAL"], "EXECUTING": ["EXECUTING"]}.get(source, ["EXECUTING", source])
                for hop in path:
                    transition_checkpoint(checkpoint["checkpoint_id"], state=hop, pause_reason="x", cfg=cfg)
            allowed = target in TRANSITIONS[source]
            try:
                transition_checkpoint(checkpoint["checkpoint_id"], state=target, pause_reason="x", cfg=cfg)
                assert allowed, f"{source}->{target} should be rejected"
            except CheckpointError as exc:
                assert not allowed and exc.status == "ERROR_CHECKPOINT_TRANSITION", f"{source}->{target}: {exc.status}"


def test_stale_expected_sequence_loses_with_conflict(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    created = _executing(cfg)
    transition_checkpoint(created["checkpoint_id"], state="EXECUTING", cfg=cfg)  # sequence 0 -> 1
    with pytest.raises(CheckpointError) as exc:
        transition_checkpoint(created["checkpoint_id"], state="PAUSED_PROVIDER", pause_reason="x", expected_sequence=0, cfg=cfg)
    assert exc.value.status == "ERROR_CHECKPOINT_CONFLICT"
    assert get_checkpoint(created["checkpoint_id"], cfg)["state"] == "EXECUTING"


def test_concurrent_resumers_exactly_one_wins(tmp_path: Path) -> None:
    import threading

    cfg = make_settings(tmp_path)
    created = _executing(cfg)
    paused = transition_checkpoint(created["checkpoint_id"], state="PAUSED_PROVIDER", pause_reason="down", cfg=cfg)
    outcomes: list[str] = []
    barrier = threading.Barrier(8)

    def resume() -> None:
        barrier.wait()
        try:
            resume_checkpoint(paused["checkpoint_id"], cfg)
            outcomes.append("won")
        except CheckpointError as exc:
            outcomes.append(exc.status)

    threads = [threading.Thread(target=resume) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("won") == 1
    assert set(outcomes) - {"won"} <= {"ERROR_CHECKPOINT_CONFLICT", "ERROR_CHECKPOINT_NOT_RESUMABLE"}
    assert get_checkpoint(paused["checkpoint_id"], cfg)["sequence"] == paused["sequence"] + 1


def test_invalid_payload_is_rejected_on_read(tmp_path: Path) -> None:
    import sqlite3

    cfg = make_settings(tmp_path)
    created = _executing(cfg)
    bad = {**created, "state": "NOT_A_STATE"}
    import json

    with sqlite3.connect(cfg.sqlite_path) as conn:
        conn.execute("UPDATE agent_checkpoints SET payload_json = ? WHERE checkpoint_id = ?", (json.dumps(bad), created["checkpoint_id"]))
    with pytest.raises(CheckpointError) as exc:
        get_checkpoint(created["checkpoint_id"], cfg)
    assert exc.value.status == "ERROR_CHECKPOINT_SCHEMA"


def test_step_in_flight_survives_crash_and_completion_is_idempotent(tmp_path: Path) -> None:
    from backend.app.agent_checkpoints import begin_step, complete_step

    cfg = make_settings(tmp_path)
    created = _executing(cfg, pending_steps=[{"id": "fetch"}])
    begun = begin_step(created["checkpoint_id"], step_id="fetch", idempotency_key="k-1", cfg=cfg)
    assert begun["in_flight_step"]["idempotency_key"] == "k-1"
    with pytest.raises(CheckpointError) as busy:
        begin_step(created["checkpoint_id"], step_id="other", idempotency_key="k-2", cfg=cfg)
    assert busy.value.status == "ERROR_CHECKPOINT_STEP_IN_FLIGHT"

    paused = transition_checkpoint(created["checkpoint_id"], state="PAUSED_PROVIDER", pause_reason="crash", cfg=cfg)
    resumed = resume_checkpoint(created["checkpoint_id"], cfg)
    assert paused["in_flight_step"] == resumed["in_flight_step"] == begun["in_flight_step"]  # resume can see what was mid-flight

    done = complete_step(created["checkpoint_id"], idempotency_key="k-1", result={"ok": True}, cfg=cfg)
    again = complete_step(created["checkpoint_id"], idempotency_key="k-1", cfg=cfg)
    assert done["in_flight_step"] is None and done["pending_steps"] == []
    assert len(again["completed_steps"]) == 1
    with pytest.raises(CheckpointError) as replay:
        begin_step(created["checkpoint_id"], step_id="fetch", idempotency_key="k-1", cfg=cfg)
    assert replay.value.status == "ERROR_CHECKPOINT_STEP_ALREADY_COMPLETED"


def test_parallel_step_batch_persists_all_in_flight_steps_and_closes_idempotently(tmp_path: Path) -> None:
    from backend.app.agent_checkpoints import begin_steps, complete_step

    cfg = make_settings(tmp_path)
    created = _executing(cfg, pending_steps=[{"id": "a"}, {"id": "b"}])
    begun = begin_steps(
        created["checkpoint_id"],
        steps=[
            {"step_id": "a", "idempotency_key": "k-a"},
            {"step_id": "b", "idempotency_key": "k-b"},
        ],
        cfg=cfg,
    )
    assert [step["step_id"] for step in begun["in_flight_steps"]] == ["a", "b"]
    assert get_checkpoint(created["checkpoint_id"], cfg)["in_flight_steps"] == begun["in_flight_steps"]

    after_a = complete_step(created["checkpoint_id"], idempotency_key="k-a", result={"ok": True}, cfg=cfg)
    assert [step["step_id"] for step in after_a["in_flight_steps"]] == ["b"]
    after_b = complete_step(created["checkpoint_id"], idempotency_key="k-b", result={"ok": True}, cfg=cfg)
    again = complete_step(created["checkpoint_id"], idempotency_key="k-b", result={"changed": True}, cfg=cfg)
    assert after_b["in_flight_steps"] == []
    assert after_b["pending_steps"] == []
    assert again == after_b


def test_completing_a_step_twice_writes_nothing(tmp_path: Path) -> None:
    from backend.app.agent_checkpoints import begin_step, complete_step

    cfg = make_settings(tmp_path)
    created = _executing(cfg, pending_steps=[{"id": "fetch"}])
    begin_step(created["checkpoint_id"], step_id="fetch", idempotency_key="k-1", cfg=cfg)
    done = complete_step(created["checkpoint_id"], idempotency_key="k-1", cfg=cfg)
    again = complete_step(created["checkpoint_id"], idempotency_key="k-1", cfg=cfg)
    # A true no-op keeps the sequence, so whoever holds it for a compare-and-swap does not lose.
    assert again == done == get_checkpoint(created["checkpoint_id"], cfg)
    after = transition_checkpoint(created["checkpoint_id"], state="EXECUTING", expected_sequence=done["sequence"], cfg=cfg)
    assert after["sequence"] == done["sequence"] + 1


def test_cannot_complete_with_step_in_flight(tmp_path: Path) -> None:
    from backend.app.agent_checkpoints import begin_step

    cfg = make_settings(tmp_path)
    created = _executing(cfg)
    begin_step(created["checkpoint_id"], step_id="s1", idempotency_key="k", cfg=cfg)
    with pytest.raises(CheckpointError) as exc:
        transition_checkpoint(created["checkpoint_id"], state="COMPLETED", cfg=cfg)
    assert exc.value.status == "ERROR_CHECKPOINT_STEP_IN_FLIGHT"


def test_contradictions_survive_replacement_and_block_completion_until_adjudicated(tmp_path: Path) -> None:
    from backend.app.agent_checkpoints import adjudicate_contradiction, record_contradiction

    cfg = make_settings(tmp_path)
    created = _executing(cfg)
    recorded = record_contradiction(created["checkpoint_id"], contradiction_id="c-1", claims=["A is 3", "A is 4"], cfg=cfg)
    assert recorded["contradictions"][0]["state"] == "UNRESOLVED"

    with pytest.raises(CheckpointError) as dropped:
        transition_checkpoint(created["checkpoint_id"], state="EXECUTING", contradictions=[], cfg=cfg)
    assert dropped.value.status == "ERROR_CHECKPOINT_CONTRADICTION_DROPPED"
    with pytest.raises(CheckpointError) as blocked:
        transition_checkpoint(created["checkpoint_id"], state="COMPLETED", cfg=cfg)
    assert blocked.value.status == "ERROR_CHECKPOINT_OPEN_CONTRADICTIONS"

    adjudicated = adjudicate_contradiction(created["checkpoint_id"], contradiction_id="c-1", resolution="A is 4", rationale="newer source", adjudicated_by="operator", cfg=cfg)
    item = adjudicated["contradictions"][0]
    assert item["claims"] == ["A is 3", "A is 4"]  # originals are never erased
    assert item["state"] == "ADJUDICATED" and item["resolution"] == "A is 4"
    assert transition_checkpoint(created["checkpoint_id"], state="COMPLETED", cfg=cfg)["state"] == "COMPLETED"
    with pytest.raises(CheckpointError):
        adjudicate_contradiction(created["checkpoint_id"], contradiction_id="c-1", resolution="x", rationale="y", adjudicated_by="z", cfg=cfg)


def test_checkpoints_start_only_in_an_initial_state_and_are_validated_before_writing(tmp_path: Path) -> None:
    from backend.app.agent_checkpoints import list_checkpoints

    cfg = make_settings(tmp_path)
    for state in ("COMPLETED", "FAILED", "PAUSED_PROVIDER", "BLOCKED_EXTERNAL"):
        with pytest.raises(CheckpointError) as exc:
            create_checkpoint(session_id="session-1", run_id="run-1", objective="Audit", source_sha=SHA, state=state, cfg=cfg)
        assert exc.value.status == "ERROR_CHECKPOINT_INITIAL_STATE", state
    with pytest.raises(CheckpointError) as invalid:
        create_checkpoint(session_id="session-1", run_id="run-1", objective="Audit", source_sha=SHA, completed_steps=[1], cfg=cfg)
    assert invalid.value.status == "ERROR_CHECKPOINT_SCHEMA"
    with pytest.raises(CheckpointError) as forged:
        create_checkpoint(session_id="session-1", run_id="run-1", objective="Audit", source_sha=SHA, contradictions=[{"id": "c-1", "state": "ADJUDICATED"}], cfg=cfg)
    assert forged.value.status == "ERROR_CHECKPOINT_CONTRADICTION_STATE"
    assert list_checkpoints(cfg) == []
    for state in ("PLANNING", "READY_FOR_APPROVAL", "EXECUTING"):
        assert create_checkpoint(session_id="session-1", run_id="run-1", objective="Audit", source_sha=SHA, state=state, cfg=cfg)["state"] == state


def test_contradictions_change_only_through_adjudication(tmp_path: Path) -> None:
    from backend.app.agent_checkpoints import record_contradiction

    cfg = make_settings(tmp_path)
    created = _executing(cfg)
    recorded = record_contradiction(created["checkpoint_id"], contradiction_id="c-1", claims=["A is 3", "A is 4"], cfg=cfg)
    original = recorded["contradictions"][0]

    attempts = {
        "ERROR_CHECKPOINT_CONTRADICTION_REWRITTEN": [{"id": "c-1", "state": "ADJUDICATED"}],
        "ERROR_CHECKPOINT_CONTRADICTION_DUPLICATE": [original, {"id": "c-1", "state": "ADJUDICATED"}],
        "ERROR_CHECKPOINT_CONTRADICTION_STATE": [original, {"id": "c-2", "state": "ADJUDICATED", "resolution": "x"}],
        "ERROR_CHECKPOINT_CONTRADICTION_ID": [original, {"state": "UNRESOLVED"}],
    }
    for status, supplied in attempts.items():
        with pytest.raises(CheckpointError) as exc:
            transition_checkpoint(created["checkpoint_id"], state="EXECUTING", contradictions=supplied, cfg=cfg)
        assert exc.value.status == status
    assert get_checkpoint(created["checkpoint_id"], cfg)["contradictions"] == [original]
    with pytest.raises(CheckpointError) as blocked:
        transition_checkpoint(created["checkpoint_id"], state="COMPLETED", cfg=cfg)
    assert blocked.value.status == "ERROR_CHECKPOINT_OPEN_CONTRADICTIONS"

    added = transition_checkpoint(created["checkpoint_id"], state="EXECUTING", contradictions=[original, {"id": "c-2", "state": "UNRESOLVED", "claims": ["B"]}], cfg=cfg)
    assert [item["id"] for item in added["contradictions"]] == ["c-1", "c-2"]


def test_completion_counts_any_unadjudicated_record_as_open(tmp_path: Path) -> None:
    import json
    import sqlite3

    cfg = make_settings(tmp_path)
    created = _executing(cfg)
    for legacy in ({"id": "c-1", "state": "RESOLVED"}, {"id": "c-1", "state": "ADJUDICATED"}):
        stored = {**get_checkpoint(created["checkpoint_id"], cfg), "contradictions": [legacy]}
        with sqlite3.connect(cfg.sqlite_path) as conn:
            conn.execute("UPDATE agent_checkpoints SET payload_json = ? WHERE checkpoint_id = ?", (json.dumps(stored), created["checkpoint_id"]))
        with pytest.raises(CheckpointError) as exc:
            transition_checkpoint(created["checkpoint_id"], state="COMPLETED", cfg=cfg)
        assert exc.value.status == "ERROR_CHECKPOINT_OPEN_CONTRADICTIONS", legacy


def test_terminal_checkpoints_take_no_new_contradictions(tmp_path: Path) -> None:
    from backend.app.agent_checkpoints import record_contradiction

    cfg = make_settings(tmp_path)
    created = _executing(cfg)
    done = transition_checkpoint(created["checkpoint_id"], state="COMPLETED", cfg=cfg)
    with pytest.raises(CheckpointError) as exc:
        record_contradiction(created["checkpoint_id"], contradiction_id="late", claims=["X"], cfg=cfg)
    assert exc.value.status == "ERROR_CHECKPOINT_TERMINAL"
    assert get_checkpoint(created["checkpoint_id"], cfg) == done


def test_list_checkpoints_fails_closed_on_corrupt_or_invalid_rows(tmp_path: Path) -> None:
    import json
    import sqlite3

    from backend.app.agent_checkpoints import list_checkpoints

    cfg = make_settings(tmp_path)
    created = _executing(cfg)
    assert list_checkpoints(cfg) == [created]
    for payload, status in (("{not json", "ERROR_CHECKPOINT_CORRUPT"), (json.dumps({**created, "state": "NOT_A_STATE"}), "ERROR_CHECKPOINT_SCHEMA")):
        with sqlite3.connect(cfg.sqlite_path) as conn:
            conn.execute("UPDATE agent_checkpoints SET payload_json = ? WHERE checkpoint_id = ?", (payload, created["checkpoint_id"]))
        with pytest.raises(CheckpointError) as exc:
            list_checkpoints(cfg)
        assert exc.value.status == status
