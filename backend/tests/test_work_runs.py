from __future__ import annotations

import sqlite3
import threading
import time

import pytest
from fastapi.testclient import TestClient

from backend.app import work_runs
from backend.app.contracts import validate_contract
from backend.app.conversations import create_conversation, get_conversation
from backend.app.ingestion import ingest_file
from backend.app.main import create_app
from backend.app.response_flow import get_interaction, get_pending_interaction
from backend.app.tools import (
    TOOL_REGISTRY,
    ToolDefinition,
    ToolRuntimeError,
    approve_tool_call,
    deny_tool_call,
    list_tool_calls,
)
from backend.app.work_runs import (
    RunError,
    edit_queued_run,
    get_run,
    list_run_events,
    list_runs,
    reconcile_runs_on_startup,
    resume_waiting_run,
    run_envelope,
    stop_run,
    stream_run_events,
    submit_message,
)
from backend.tests.test_api import make_settings


@pytest.fixture(autouse=True)
def inline_execution():
    work_runs.set_execution_mode("inline")
    yield
    work_runs.set_execution_mode("thread")


@pytest.fixture()
def cfg(tmp_path):
    settings = make_settings(tmp_path)
    settings.ensure_dirs()
    note = tmp_path / "lineage.md"
    note.write_text("Source lineage binds every chunk to a SHA-256 source identity and a stable chunk id.", encoding="utf-8")
    ingest_file(note, settings)
    return settings


@pytest.fixture()
def conversation(cfg):
    return create_conversation("Work feed", cfg)["conversation_id"]


def _types(run_id, cfg):
    return [event["type"] for event in list_run_events(run_id, cfg=cfg)]


def _submit(conversation, prompt, cfg, **kwargs):
    return submit_message(conversation, prompt, cfg=cfg, **kwargs)["run"]


@pytest.fixture()
def flaky_tool(monkeypatch):
    """A local read-only tool that fails on its first execution and succeeds afterwards."""
    calls = {"n": 0}

    def handler(arguments, settings):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ToolRuntimeError("ERROR_TOOL_TEST_TRANSIENT", "first attempt fails")
        return {"status": "FLAKY_OK", "attempt": calls["n"]}

    monkeypatch.setitem(TOOL_REGISTRY, "test.flaky", ToolDefinition("test.flaky", "flaky", False, True, handler))
    monkeypatch.setitem(work_runs.COMMANDS, "/flaky", {"tool": "test.flaky", "usage": "/flaky", "arg": None})
    return calls


# --- F02 / TGT-002: chronological, persisted work feed ---------------------------------


def test_answer_run_streams_an_ordered_feed_and_persists_turns(cfg, conversation):
    run = _submit(conversation, "How does source lineage work?", cfg)
    run = get_run(run["run_id"], cfg)
    assert run["state"] == "COMPLETED"
    assert run["outcome"] == "ANSWERED"
    events = list_run_events(run["run_id"], cfg=cfg)
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    types = [event["type"] for event in events]
    for expected in ("queued", "run_started", "route", "plan", "retrieval", "text_delta", "citation", "artifact", "final"):
        assert expected in types
    assert types.index("plan") < types.index("retrieval") < types.index("text_delta") < types.index("final")
    assert types[-1] == "final"
    answer = "".join(event["data"]["text"] for event in events if event["type"] == "text_delta")
    turns = get_conversation(conversation, cfg)["turns"]
    assert [turn["role"] for turn in turns] == ["user", "assistant"]
    assert turns[1]["content"] == answer
    assert {turn["work_run_id"] for turn in turns} == {run["run_id"]}


def test_real_work_run_persists_validated_terminal_manifest(cfg, conversation):
    run = _submit(conversation, "How does source lineage work?", cfg)
    final = get_run(run["run_id"], cfg)
    manifest = final["run_manifest"]
    validate_contract("run_manifest", manifest)
    assert manifest["run_id"] == final["run_id"]
    assert manifest["task_id"] == conversation
    assert manifest["state"] == "COMPLETE"
    assert manifest["completed_at"] == final["finished_at"]


def test_work_run_manifest_drift_fails_closed(cfg, conversation):
    run = _submit(conversation, "How does source lineage work?", cfg)
    final = get_run(run["run_id"], cfg)
    with sqlite3.connect(cfg.sqlite_path) as conn:
        row = conn.execute("SELECT payload_json FROM work_runs WHERE run_id = ?", (final["run_id"],)).fetchone()
        payload = __import__("json").loads(row[0])
        payload["run_manifest"]["state"] = "PLANNING"
        conn.execute(
            "UPDATE work_runs SET payload_json = ? WHERE run_id = ?",
            (__import__("json").dumps(payload, sort_keys=True), final["run_id"]),
        )
    with pytest.raises(RunError) as exc:
        get_run(final["run_id"], cfg)
    assert exc.value.status == "ERROR_RUN_MANIFEST_DRIFT"


def test_idempotent_resubmission_returns_the_original_run(cfg, conversation):
    first = submit_message(conversation, "How does source lineage work?", idempotency_key="client-key-0001", cfg=cfg)
    again = submit_message(conversation, "How does source lineage work?", idempotency_key="client-key-0001", cfg=cfg)
    assert again["status"] == "RUN_DUPLICATE_RETURNED"
    assert again["run"]["run_id"] == first["run"]["run_id"]
    assert len(get_conversation(conversation, cfg)["turns"]) == 2


@pytest.mark.parametrize(
    "prompt,kwargs,status",
    [
        ("   ", {}, "ERROR_RUN_PROMPT_EMPTY"),
        ("x" * 20001, {}, "ERROR_RUN_PROMPT_TOO_LONG"),
        ("hello", {"mode": "yolo"}, "ERROR_RUN_MODE_INVALID"),
    ],
)
def test_invalid_messages_are_rejected(cfg, conversation, prompt, kwargs, status):
    with pytest.raises(RunError) as exc:
        submit_message(conversation, prompt, cfg=cfg, **kwargs)
    assert exc.value.status == status


def test_message_to_unknown_conversation_is_rejected(cfg):
    with pytest.raises(RunError) as exc:
        submit_message("conv_missing", "hello", cfg=cfg)
    assert exc.value.status == "ERROR_CONVERSATION_NOT_FOUND"


# --- F07 / F06: approval-bound actions and execution modes ------------------------------


def test_ask_mode_waits_for_approval_then_resumes(cfg, conversation):
    run = _submit(conversation, "/status", cfg)
    assert run["state"] == "AWAITING_APPROVAL"
    request = next(event for event in list_run_events(run["run_id"], cfg=cfg) if event["type"] == "approval_request")
    assert request["data"]["tool_name"] == "source_library.status"
    assert len(request["data"]["arguments_sha256"]) == 64
    approve_tool_call(request["data"]["tool_call_id"], cfg=cfg, expected_arguments_sha256=request["data"]["arguments_sha256"])
    resume_waiting_run(run["run_id"], cfg)
    run = get_run(run["run_id"], cfg)
    assert run["state"] == "COMPLETED"
    assert run["outcome"] == "ACTIONS_COMPLETED"
    assert "tool_result" in _types(run["run_id"], cfg)


def test_plan_mode_executes_nothing(cfg, conversation):
    run = get_run(_submit(conversation, "/status\nHow does lineage work?", cfg, mode="plan")["run_id"], cfg)
    assert run["outcome"] == "PLAN_ONLY"
    assert list_tool_calls(cfg)["tool_calls"] == []
    plan = next(event for event in list_run_events(run["run_id"], cfg=cfg) if event["type"] == "plan")
    assert plan["data"]["steps"][0].get("tool") == "source_library.status"


def test_auto_mode_preapproves_only_local_read_only_tools(cfg, conversation):
    run = _submit(conversation, "/status\n/fetch https://example.com/", cfg, mode="auto")
    calls = {call["tool_name"]: call for call in list_tool_calls(cfg)["tool_calls"]}
    assert calls["source_library.status"]["approved_by"] == "policy:auto-local-read-only"
    assert calls["web.fetch"]["status"] == "PENDING_APPROVAL"
    assert get_run(run["run_id"], cfg)["state"] == "AWAITING_APPROVAL"
    deny_tool_call(calls["web.fetch"]["tool_call_id"], cfg=cfg)
    resume_waiting_run(run["run_id"], cfg)
    final = get_run(run["run_id"], cfg)
    assert final["state"] == "COMPLETED"
    assert final["outcome"] == "ACTIONS_COMPLETED"


# --- F08 / F16: truthful per-call outcomes, failure question and targeted retry ---------


def test_denied_action_is_a_distinct_outcome_and_counts_close(cfg, conversation):
    run = _submit(conversation, "/status\n/memory", cfg)
    calls = {call["tool_name"]: call for call in list_tool_calls(cfg)["tool_calls"]}
    approve_tool_call(calls["source_library.status"]["tool_call_id"], cfg=cfg)
    deny_tool_call(calls["memory.list"]["tool_call_id"], cfg=cfg)
    resume_waiting_run(run["run_id"], cfg)
    events = list_run_events(run["run_id"], cfg=cfg)
    results = {event["data"]["tool_name"]: event["data"] for event in events if event["type"] == "tool_result"}
    assert results["memory.list"]["status"] == "DENIED"
    assert results["memory.list"]["error"]["category"] == "BLOCKED_ACTION"
    summary = next(event for event in events if event["type"] == "tool_summary")["data"]
    assert summary["by_status"]["EXECUTED"] == 1 and summary["by_status"]["DENIED"] == 1
    assert summary["arithmetic_closes"] is True
    assert "question" not in [event["type"] for event in events]
    denied = next(call for call in list_tool_calls(cfg)["tool_calls"] if call["tool_name"] == "memory.list")
    assert denied["executed_at"] is None


def test_failed_action_asks_and_retry_repeats_only_the_failed_target(cfg, conversation, flaky_tool):
    client = TestClient(create_app(cfg))
    run = _submit(conversation, "/status\n/flaky", cfg, mode="auto")
    run = get_run(run["run_id"], cfg)
    assert run["state"] == "AWAITING_INPUT"
    question = next(event for event in list_run_events(run["run_id"], cfg=cfg) if event["type"] == "question")["data"]
    assert [option["option_id"] for option in question["options"]] == ["retry_failed", "continue", "stop"]
    assert all(option["consequence"] for option in question["options"])
    status_call = next(call for call in list_tool_calls(cfg)["tool_calls"] if call["tool_name"] == "source_library.status")
    resolved = client.post(f"/interactions/{question['interaction_id']}/resolve", json={"option_id": "retry_failed", "expected_version": question["version"]})
    assert resolved.status_code == 200
    run = get_run(run["run_id"], cfg)
    assert run["state"] == "COMPLETED", run
    calls = list_tool_calls(cfg)["tool_calls"]
    flaky = [call for call in calls if call["tool_name"] == "test.flaky"]
    assert [call["status"] for call in flaky] == ["FAILED", "EXECUTED"]
    assert flaky[1]["retry_of"] == flaky[0]["tool_call_id"]
    assert flaky[1]["approved_by"] == "policy:auto-local-read-only"
    assert [call["tool_call_id"] for call in calls if call["tool_name"] == "source_library.status"] == [status_call["tool_call_id"]]
    assert flaky_tool["n"] == 2
    interaction = get_interaction(question["interaction_id"], cfg)
    assert interaction["status"] == "RESOLVED"
    assert interaction["resolution"]["option_id"] == "retry_failed"


def test_retry_in_ask_mode_needs_a_fresh_approval(cfg, conversation, flaky_tool):
    run = _submit(conversation, "/flaky", cfg)
    first = list_tool_calls(cfg)["tool_calls"][0]
    approve_tool_call(first["tool_call_id"], cfg=cfg)
    resume_waiting_run(run["run_id"], cfg)
    question = get_pending_interaction(conversation, cfg)
    from backend.app.response_flow import resolve_interaction

    resolve_interaction(question["interaction_id"], expected_version=question["version"], resolved_by="user", option_id="retry_failed", cfg=cfg)
    resume_waiting_run(run["run_id"], cfg)
    run = get_run(run["run_id"], cfg)
    assert run["state"] == "AWAITING_APPROVAL"
    retry = list_tool_calls(cfg)["tool_calls"][-1]
    assert retry["status"] == "PENDING_APPROVAL"
    assert retry["retry_of"] == first["tool_call_id"]


@pytest.mark.parametrize("choice,outcome", [("continue", "ACTIONS_COMPLETED"), ("stop", "STOPPED_BY_USER_CHOICE")])
def test_continue_and_stop_choices(cfg, conversation, flaky_tool, choice, outcome):
    from backend.app.response_flow import resolve_interaction

    run = _submit(conversation, "/flaky", cfg, mode="auto")
    question = get_pending_interaction(conversation, cfg)
    resolve_interaction(question["interaction_id"], expected_version=question["version"], resolved_by="user", option_id=choice, cfg=cfg)
    resume_waiting_run(run["run_id"], cfg)
    assert get_run(run["run_id"], cfg)["outcome"] == outcome
    assert flaky_tool["n"] == 1


def test_freeform_answer_is_recorded_as_a_user_note(cfg, conversation, flaky_tool):
    from backend.app.response_flow import resolve_interaction

    run = _submit(conversation, "/flaky", cfg, mode="auto")
    question = get_pending_interaction(conversation, cfg)
    resolve_interaction(question["interaction_id"], expected_version=question["version"], resolved_by="user", freeform_text="skip it for now", cfg=cfg)
    resume_waiting_run(run["run_id"], cfg)
    notes = [event["data"]["text"] for event in list_run_events(run["run_id"], cfg=cfg) if event["type"] == "user_note"]
    assert notes == ["skip it for now"]
    assert flaky_tool["n"] == 1


def test_stale_and_duplicate_question_answers_are_rejected(cfg, conversation, flaky_tool):
    client = TestClient(create_app(cfg))
    _submit(conversation, "/flaky", cfg, mode="auto")
    question = get_pending_interaction(conversation, cfg)
    ok = client.post(f"/interactions/{question['interaction_id']}/resolve", json={"option_id": "stop", "expected_version": question["version"]})
    assert ok.status_code == 200
    duplicate = client.post(f"/interactions/{question['interaction_id']}/resolve", json={"option_id": "stop", "expected_version": question["version"]})
    assert duplicate.status_code == 409


# --- F04: queue for after this turn ----------------------------------------------------


def test_messages_queue_fifo_and_dequeue_exactly_once(cfg, conversation):
    blocking = _submit(conversation, "/status", cfg)
    second = _submit(conversation, "How does source lineage work?", cfg)
    third = _submit(conversation, "What is a chunk id?", cfg)
    fourth = _submit(conversation, "Cancel me", cfg)
    assert [get_run(r["run_id"], cfg)["state"] for r in (second, third, fourth)] == ["QUEUED"] * 3
    stale_version = get_run(third["run_id"], cfg)["version"]
    edited = edit_queued_run(third["run_id"], "What is a stable chunk id?", expected_version=stale_version, cfg=cfg)
    assert edited["prompt"] == "What is a stable chunk id?"
    with pytest.raises(RunError) as exc:
        edit_queued_run(third["run_id"], "again", expected_version=stale_version, cfg=cfg)
    assert exc.value.status == "ERROR_RUN_VERSION_CONFLICT"
    assert stop_run(fourth["run_id"], cfg=cfg)["state"] == "CANCELLED"
    approve_tool_call(get_run(blocking["run_id"], cfg)["tool_call_ids"][0], cfg=cfg)
    resume_waiting_run(blocking["run_id"], cfg)
    states = {name: get_run(r["run_id"], cfg) for name, r in {"b": blocking, "2": second, "3": third, "4": fourth}.items()}
    assert states["b"]["state"] == states["2"]["state"] == states["3"]["state"] == "COMPLETED"
    assert states["4"]["state"] == "CANCELLED"
    assert states["2"]["started_at"] <= states["3"]["started_at"]
    user_turns = [turn["content"] for turn in get_conversation(conversation, cfg)["turns"] if turn["role"] == "user"]
    assert user_turns == ["/status", "How does source lineage work?", "What is a stable chunk id?"]
    with pytest.raises(RunError) as exc:
        edit_queued_run(second["run_id"], "late edit", expected_version=states["2"]["version"], cfg=cfg)
    assert exc.value.status == "ERROR_RUN_NOT_QUEUED"


# --- F03: stop, interruption and in-flight distinction ----------------------------------


def test_stop_while_awaiting_approval_interrupts_and_cancels_pending_calls(cfg, conversation):
    run = _submit(conversation, "/status\n/memory", cfg)
    queued = _submit(conversation, "How does source lineage work?", cfg)
    stopped = stop_run(run["run_id"], cfg=cfg)
    assert stopped["state"] == "INTERRUPTED"
    assert {call["status"] for call in list_tool_calls(cfg)["tool_calls"]} == {"CANCELLED"}
    interrupted = next(event for event in list_run_events(run["run_id"], cfg=cfg) if event["type"] == "interrupted")
    assert len(interrupted["data"]["cancelled_tool_call_ids"]) == 2
    assert get_run(queued["run_id"], cfg)["state"] == "COMPLETED"
    with pytest.raises(RunError) as exc:
        stop_run(run["run_id"], cfg=cfg)
    assert exc.value.status == "ERROR_RUN_ALREADY_FINISHED"


def test_stop_during_a_running_tool_records_it_in_flight_and_halts_later_steps(cfg, conversation, monkeypatch):
    work_runs.set_execution_mode("thread")
    started = threading.Event()
    release = threading.Event()

    def slow(arguments, settings):
        started.set()
        release.wait(5)
        return {"status": "SLOW_OK"}

    monkeypatch.setitem(TOOL_REGISTRY, "test.slow", ToolDefinition("test.slow", "slow", False, True, slow))
    monkeypatch.setitem(work_runs.COMMANDS, "/slow", {"tool": "test.slow", "usage": "/slow", "arg": None})
    run = _submit(conversation, "/slow\n/status\nHow does source lineage work?", cfg, mode="auto")
    assert started.wait(5)
    stop_run(run["run_id"], cfg=cfg)
    release.set()
    for _ in range(100):
        if get_run(run["run_id"], cfg)["state"] == "INTERRUPTED":
            break
        time.sleep(0.05)
    final = get_run(run["run_id"], cfg)
    assert final["state"] == "INTERRUPTED"
    interrupted = next(event for event in list_run_events(run["run_id"], cfg=cfg) if event["type"] == "interrupted")
    slow_call = next(call for call in list_tool_calls(cfg)["tool_calls"] if call["tool_name"] == "test.slow")
    assert interrupted["data"]["in_flight_at_stop"] == [slow_call["tool_call_id"]]
    assert slow_call["status"] == "EXECUTED"
    status_call = next(call for call in list_tool_calls(cfg)["tool_calls"] if call["tool_name"] == "source_library.status")
    assert status_call["status"] == "CANCELLED"
    assert "text_delta" not in _types(run["run_id"], cfg)


def test_interrupt_flag_supersedes_the_active_run(cfg, conversation):
    active = _submit(conversation, "/status", cfg)
    newer = submit_message(conversation, "How does source lineage work?", interrupt=True, cfg=cfg)["run"]
    assert get_run(active["run_id"], cfg)["outcome"] == "SUPERSEDED_BY_NEW_MESSAGE"
    assert get_run(newer["run_id"], cfg)["state"] == "COMPLETED"


# --- F14: pending question across a new message ----------------------------------------


def test_new_message_keeps_the_pending_question_unless_dismissed(cfg, conversation, flaky_tool):
    waiting = _submit(conversation, "/flaky", cfg, mode="auto")
    question = get_pending_interaction(conversation, cfg)
    queued = _submit(conversation, "How does source lineage work?", cfg)
    assert get_run(queued["run_id"], cfg)["state"] == "QUEUED"
    same = get_pending_interaction(conversation, cfg)
    assert (same["interaction_id"], same["version"]) == (question["interaction_id"], question["version"])
    submit_message(conversation, "Ask everything again", dismiss_pending_question=True, cfg=cfg)
    assert get_interaction(question["interaction_id"], cfg)["status"] == "DISMISSED"
    assert get_run(waiting["run_id"], cfg)["outcome"] == "QUESTION_DISMISSED"
    assert get_run(queued["run_id"], cfg)["state"] == "COMPLETED"
    assert flaky_tool["n"] == 1


# --- TGT-018: restart and resume ----------------------------------------------------------


def test_restart_interrupts_running_runs_and_resumes_waiting_ones(cfg, conversation):
    waiting = _submit(conversation, "/status", cfg)
    other = create_conversation("Other", cfg)["conversation_id"]
    stuck = _submit(other, "/memory", cfg)
    with sqlite3.connect(cfg.sqlite_path) as conn:
        conn.execute("UPDATE work_runs SET state = 'RUNNING' WHERE run_id = ?", (stuck["run_id"],))
    result = reconcile_runs_on_startup(cfg)
    assert result["interrupted"] == [stuck["run_id"]]
    assert get_run(stuck["run_id"], cfg)["outcome"] == "PROCESS_RESTART"
    assert get_run(waiting["run_id"], cfg)["state"] == "AWAITING_APPROVAL"
    approve_tool_call(get_run(waiting["run_id"], cfg)["tool_call_ids"][0], cfg=cfg)
    resume_waiting_run(waiting["run_id"], cfg)
    assert get_run(waiting["run_id"], cfg)["state"] == "COMPLETED"


def test_crashing_step_ends_the_run_failed_with_a_category(cfg, conversation):
    run = get_run(_submit(conversation, "How does lineage work?", cfg, options={"model_provider": "no-such-provider"})["run_id"], cfg)
    assert run["state"] == "FAILED"
    assert run["error"]["category"] in {"MODEL_UNAVAILABLE", "BLOCKED_ACTION"}
    assert _types(run["run_id"], cfg)[-1] == "final"


def test_unknown_command_is_reported_not_silently_dropped(cfg, conversation):
    run = _submit(conversation, "/teleport now\nHow does lineage work?", cfg)
    warnings = [event["data"] for event in list_run_events(run["run_id"], cfg=cfg) if event["type"] == "warning"]
    assert {"status": "WARN_UNKNOWN_COMMAND", "category": "INPUT_INVALID", "detail": "/teleport"} in warnings


# --- F09 / F15 / F18: projections -------------------------------------------------------


def test_run_envelope_takes_tool_state_from_records(cfg, conversation):
    run = _submit(conversation, "/status\n/memory", cfg)
    calls = {call["tool_name"]: call for call in list_tool_calls(cfg)["tool_calls"]}
    approve_tool_call(calls["source_library.status"]["tool_call_id"], cfg=cfg)
    deny_tool_call(calls["memory.list"]["tool_call_id"], cfg=cfg)
    resume_waiting_run(run["run_id"], cfg)
    envelope = run_envelope(run["run_id"], cfg)
    results = {part["tool_call_id"]: part["status"] for part in envelope["parts"] if part["kind"] == "tool_result"}
    assert results == {calls["source_library.status"]["tool_call_id"]: "EXECUTED", calls["memory.list"]["tool_call_id"]: "DENIED"}
    receipt = envelope["parts"][-1]
    assert receipt["kind"] == "task_receipt"
    assert receipt["summary"]["tool_summary"]["arithmetic_closes"] is True


def test_background_task_list_paginates_with_receipt_metrics(cfg, conversation):
    for prompt in ("How does lineage work?", "What is a chunk?", "What is a source?"):
        _submit(conversation, prompt, cfg)
    page = list_runs(limit=2, cfg=cfg)
    assert len(page["runs"]) == 2
    assert page["next_before_seq"] is not None
    rest = list_runs(limit=2, before_seq=page["next_before_seq"], cfg=cfg)
    assert len(rest["runs"]) == 1 and rest["next_before_seq"] is None
    assert all(row["state"] == "COMPLETED" and row["event_count"] > 0 for row in page["runs"] + rest["runs"])
    assert list_runs(states=["FAILED"], cfg=cfg)["runs"] == []


# --- API: messages, SSE replay, stop, auth ------------------------------------------------


def test_api_message_stream_replays_and_ends(cfg, conversation):
    client = TestClient(create_app(cfg))
    response = client.post(f"/conversations/{conversation}/messages", json={"prompt": "How does source lineage work?"})
    assert response.status_code == 200
    run_id = response.json()["run"]["run_id"]
    stream = client.get(f"/work-runs/{run_id}/events/stream")
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    body = stream.text
    assert "event: final" in body and "event: end" in body
    total = len(list_run_events(run_id, cfg=cfg))
    tail = client.get(f"/work-runs/{run_id}/events/stream", headers={"Last-Event-ID": str(total - 1)}).text
    assert tail.count("\nevent: ") + tail.startswith("event: ") <= 2
    assert "event: final" in tail
    envelope = client.get(f"/conversations/{conversation}/envelope").json()
    assert [row["turn_id"] for row in envelope["run_envelopes"]] == [run_id]
    modes = client.get("/work-runs/modes").json()
    assert {row["mode"] for row in modes["modes"]} == {"plan", "ask", "auto"}


def test_api_tool_decision_resumes_the_waiting_run(cfg, conversation):
    client = TestClient(create_app(cfg))
    run_id = client.post(f"/conversations/{conversation}/messages", json={"prompt": "/status"}).json()["run"]["run_id"]
    call = list_tool_calls(cfg)["tool_calls"][0]
    approved = client.post(f"/tools/requests/{call['tool_call_id']}/approve", json={"arguments_sha256": call["arguments_sha256"]})
    assert approved.status_code == 200
    assert client.get(f"/work-runs/{run_id}").json()["run"]["state"] == "COMPLETED"
    stop = client.post(f"/work-runs/{run_id}/stop")
    assert stop.status_code == 409


def test_api_message_routes_require_the_operator_token(tmp_path):
    settings = make_settings(tmp_path, operator_token="t" * 40)
    client = TestClient(create_app(settings))
    conversation = client.post("/conversations", json={"title": "x"}, headers={"X-Mucaro-Token": "t" * 40}).json()["conversation_id"]
    assert client.post(f"/conversations/{conversation}/messages", json={"prompt": "hi"}).status_code == 401
    assert client.get("/work-runs").status_code == 401
    assert client.get("/work-runs/modes").status_code == 401


# --- event stream delivery --------------------------------------------------------------


def test_event_stream_wakes_on_a_commit_instead_of_waiting_out_its_poll(cfg, conversation):
    run = _submit(conversation, "/status", cfg)
    assert run["state"] == "AWAITING_APPROVAL"
    last_seq = list_run_events(run["run_id"], cfg=cfg)[-1]["seq"]
    received: list[tuple[str, float]] = []

    def follow() -> None:
        for block in stream_run_events(run["run_id"], last_seq, cfg, poll_seconds=30):
            received.append((block, time.monotonic()))

    follower = threading.Thread(target=follow, daemon=True)
    follower.start()
    time.sleep(0.2)  # let the stream reach its wait
    emitted_at = time.monotonic()
    work_runs._emit_only(run["run_id"], cfg, "warning", {"status": "WARN_TEST", "category": "INPUT_INVALID"})
    deadline = emitted_at + 5
    while not received and time.monotonic() < deadline:
        time.sleep(0.01)
    assert received, "the committed event never reached the stream"
    assert "\nevent: warning\n" in received[0][0]
    assert received[0][1] - emitted_at < 2  # far inside the 30 s poll interval
    stop_run(run["run_id"], cfg=cfg)
    follower.join(5)
    assert not follower.is_alive()
    assert received[-1][0].startswith("event: end")


def test_event_stream_keeps_events_of_a_run_that_finishes_between_its_reads(cfg, conversation, monkeypatch):
    run = _submit(conversation, "/status", cfg)
    last_seq = list_run_events(run["run_id"], cfg=cfg)[-1]["seq"]
    finished: list[bool] = []

    def finish_after_first_read(read):
        def wrapper(*args, **kwargs):
            result = read(*args, **kwargs)
            if not finished:
                finished.append(True)
                stop_run(run["run_id"], cfg=cfg)
            return result

        return wrapper

    # Whichever read the stream makes first, the run reaches a terminal state right after it.
    monkeypatch.setattr(work_runs, "get_run", finish_after_first_read(work_runs.get_run))
    monkeypatch.setattr(work_runs, "list_run_events", finish_after_first_read(work_runs.list_run_events))
    blocks = list(stream_run_events(run["run_id"], last_seq, cfg, poll_seconds=0.01))
    delivered = [block.split("\n")[1] for block in blocks if block.startswith("id:")]
    assert "event: interrupted" in delivered
    assert blocks[-1].startswith("event: end")

