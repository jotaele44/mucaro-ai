"""Claude chat agent: streaming tool loop, progress lines, approvals and history safety.

The Anthropic API is replaced by a scripted stream of server-sent events in the
wire format the Messages API uses, so these tests never touch the network or
the macOS Keychain.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Self

import pytest

from backend.app import agent, work_runs
from backend.app.agent_store import load_history, load_history_rows
from backend.app.app_settings import save_app_settings
from backend.app.conversations import create_conversation, delete_conversation, get_conversation
from backend.app.memory import list_memory_items
from backend.app.permissions import grant_directory
from backend.app.tools import approve_tool_call, deny_tool_call, list_tool_calls
from backend.app.work_runs import (
    get_run,
    list_run_events,
    reconcile_runs_on_startup,
    resume_waiting_run,
    run_envelope,
    stop_run,
    submit_message,
)
from backend.tests.test_api import make_settings

FAKE_KEY = "sk-ant-api03-" + "x" * 40


# --- a scripted Messages API -------------------------------------------------------------


def text(value: str, citations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"type": "text", "text": value, "citations": citations or []}


def thinking(note: str, signature: str = "sig-1") -> dict[str, Any]:
    return {"type": "thinking", "thinking": note, "signature": signature}


def tool_use(tool_id: str, name: str, value: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": value}


def server_tool_use(tool_id: str, name: str, value: dict[str, Any]) -> dict[str, Any]:
    return {"type": "server_tool_use", "id": tool_id, "name": name, "input": value}


def web_results(tool_id: str, urls: list[str]) -> dict[str, Any]:
    return {
        "type": "web_search_tool_result",
        "tool_use_id": tool_id,
        "content": [{"type": "web_search_result", "url": url, "title": f"Page {index}", "encrypted_content": "enc", "page_age": None}
                    for index, url in enumerate(urls)],
    }


def message(blocks: list[dict[str, Any]], stop_reason: str = "end_turn", **extra: Any) -> list[dict[str, Any]]:
    """The event sequence the API streams for one assistant message."""
    usage = {"input_tokens": 1000, "output_tokens": 5, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    events: list[dict[str, Any]] = [
        {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                                              "content": [], "stop_reason": None, "usage": usage}},
    ]
    for index, block in enumerate(blocks):
        kind = block["type"]
        if kind == "text":
            events.append({"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}})
            for citation in block.get("citations") or []:
                events.append({"type": "content_block_delta", "index": index, "delta": {"type": "citations_delta", "citation": citation}})
            value = block["text"]
            for start in range(0, len(value), 7):
                events.append({"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": value[start:start + 7]}})
        elif kind == "thinking":
            events.append({"type": "content_block_start", "index": index, "content_block": {"type": "thinking", "thinking": "", "signature": ""}})
            events.append({"type": "content_block_delta", "index": index, "delta": {"type": "thinking_delta", "thinking": block["thinking"]}})
            events.append({"type": "content_block_delta", "index": index, "delta": {"type": "signature_delta", "signature": block["signature"]}})
        elif kind in {"tool_use", "server_tool_use"}:
            events.append({"type": "content_block_start", "index": index, "content_block": {**block, "input": {}}})
            raw = json.dumps(block["input"])
            events.append({"type": "content_block_delta", "index": index, "delta": {"type": "input_json_delta", "partial_json": raw[:5]}})
            events.append({"type": "content_block_delta", "index": index, "delta": {"type": "input_json_delta", "partial_json": raw[5:]}})
        else:
            events.append({"type": "content_block_start", "index": index, "content_block": block})
        events.append({"type": "content_block_stop", "index": index})
    delta: dict[str, Any] = {"stop_reason": stop_reason, "stop_sequence": None, **extra}
    events.append({"type": "message_delta", "delta": delta, "usage": {"output_tokens": 50}})
    events.append({"type": "message_stop"})
    return events


class FakeResponse:
    def __init__(self, events: list[dict[str, Any]], on_line: Any = None) -> None:
        self.events = events
        self.on_line = on_line
        self.closed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def iter_lines(self):
        for event in self.events:
            if self.closed:
                raise RuntimeError("stream closed")
            if self.on_line is not None:
                self.on_line(event)
            yield f"event: {event['type']}"
            yield f"data: {json.dumps(event)}"
            yield ""

    def close(self) -> None:
        self.closed = True


class FakeAPI:
    """Stands in for ``client.beta.messages.with_streaming_response``; records every request."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []
        self.on_line: Any = None

    @property
    def beta(self) -> FakeAPI:
        return self

    @property
    def messages(self) -> FakeAPI:
        return self

    @property
    def with_streaming_response(self) -> FakeAPI:
        return self

    def create(self, **kwargs: Any) -> FakeResponse:
        self.requests.append(json.loads(json.dumps(kwargs)))
        if not self.script:
            raise AssertionError("unexpected extra request to Claude")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return FakeResponse(step, self.on_line)


@pytest.fixture(autouse=True)
def inline_execution():
    work_runs.set_execution_mode("inline")
    yield
    work_runs.set_execution_mode("thread")


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    # The Anthropic SDK is an optional extra (CI installs only "dev"); these tests never need it.
    monkeypatch.setattr("backend.app.app_settings.sdk_available", lambda: True)
    monkeypatch.setenv("MUCARO_AI_SECRET_STORE", "env")
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.delenv("MUCARO_AI_LIVE_MODE", raising=False)
    settings = make_settings(tmp_path)
    settings.ensure_dirs()
    save_app_settings({"live_consent": True}, settings)
    return settings


@pytest.fixture()
def api(monkeypatch):
    fake = FakeAPI([])
    monkeypatch.setattr(agent, "CLIENT_FACTORY", lambda key: fake)
    return fake


@pytest.fixture()
def chat(cfg):
    return create_conversation("Agent", cfg)["conversation_id"]


def ask(chat: str, prompt: str, cfg, mode: str = "agent") -> dict[str, Any]:
    return submit_message(chat, prompt, mode=mode, cfg=cfg)["run"]


def events(run_id: str, cfg, kind: str | None = None) -> list[dict[str, Any]]:
    rows = list_run_events(run_id, cfg=cfg)
    return [row for row in rows if kind is None or row["type"] == kind]


def progress(run_id: str, cfg) -> list[str]:
    return [row["data"]["text"] for row in events(run_id, cfg, "progress")]


# --- tests --------------------------------------------------------------------------------


def test_plain_answer_streams_and_is_stored(cfg, api, chat):
    api.script = [message([text("Paris is the capital of France.")])]
    run = ask(chat, "What is the capital of France?", cfg)

    run = get_run(run["run_id"], cfg)
    assert run["state"] == "COMPLETED" and run["outcome"] == "ANSWERED"
    assert progress(run["run_id"], cfg)[0] == "Thinking…"
    streamed = "".join(row["data"]["text"] for row in events(run["run_id"], cfg, "text_delta"))
    assert streamed == "Paris is the capital of France."
    usage = events(run["run_id"], cfg, "usage")[0]["data"]
    assert usage["input_tokens"] == 1000 and usage["output_tokens"] == 50 and usage["estimated_cost_usd"] > 0
    turns = get_conversation(chat, cfg)["turns"]
    assert [turn["role"] for turn in turns] == ["user", "assistant"]
    assert turns[1]["content"] == "Paris is the capital of France."
    assert turns[1]["agent_trace"]["model"] == "claude-opus-5-5"
    history = load_history(chat, cfg)
    assert [row["role"] for row in history] == ["user", "assistant"]
    assert history[0]["content"][1] == {"type": "text", "text": "What is the capital of France?"}
    assert history[0]["content"][0]["text"].startswith("<context>")


def test_request_uses_current_api_features(cfg, api, chat):
    api.script = [message([text("Hi.")])]
    ask(chat, "Hello", cfg)

    request = api.requests[0]
    assert request["model"] == "claude-opus-5-5"
    assert request["stream"] is True and request["max_tokens"] == agent.MAX_TOKENS
    assert request["thinking"]["display"] == "updates"
    assert request["thinking"]["block_binding"] == {"prefix_mismatch_behavior": "drop_block"}
    assert set(request["betas"]) == {agent.BETA_PROGRESS_UPDATES, agent.BETA_THINKING_BINDING, agent.BETA_FALLBACKS}
    assert request["extra_body"]["output_config"] == {"effort": "medium"}
    assert request["extra_body"]["fallbacks"] == "default"
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "Today is" not in request["system"][0]["text"]  # the cached prefix holds no per-request values
    names = [tool.get("name") for tool in request["tools"]]
    assert names[:3] == ["web_search", "web_fetch", "code_execution"]
    assert "search_sources" in names and "apply_patch" in names
    assert all(tool["eager_input_streaming"] for tool in request["tools"] if "input_schema" in tool)


def test_web_research_turns_into_progress_lines_and_citations(cfg, api, chat):
    citation = {"type": "web_search_result_location", "url": "https://docs.github.com/en/rest", "title": "GitHub REST API",
                "cited_text": "Use a personal access token.", "encrypted_index": "x"}
    api.script = [message([
        text("I'll check GitHub's documentation."),
        server_tool_use("srv_1", "web_search", {"query": "github api access"}),
        web_results("srv_1", ["https://docs.github.com/en/rest", "https://github.blog/x"]),
        thinking("Found the REST docs; reading the authentication page next."),
        text("Yes, through a personal access token.", [citation]),
    ])]
    run = ask(chat, "do you have access to GitHub?", cfg)

    lines = progress(run["run_id"], cfg)
    assert lines == [
        "Thinking…",
        "I'll check GitHub's documentation.",
        "Searching the web for “github api access”",
        "Found 2 results (docs.github.com, github.blog)",
        "Found the REST docs; reading the authentication page next.",
    ]
    answer = run_envelope(run["run_id"], cfg)
    texts = [part["text"] for part in answer["parts"] if part["kind"] == "text"]
    assert texts[-1] == "Yes, through a personal access token.[1]"
    cites = events(run["run_id"], cfg, "citation")
    assert [row["data"]["source_url"] for row in cites] == ["https://docs.github.com/en/rest"]
    assert get_conversation(chat, cfg)["turns"][1]["content"] == "Yes, through a personal access token.[1]"


def test_safe_local_tool_runs_without_asking(cfg, api, chat):
    api.script = [
        message([tool_use("tu_1", "search_sources", {"query": "budget"})], "tool_use"),
        message([text("Your documents don't mention a budget.")]),
    ]
    run = ask(chat, "What does my budget say?", cfg)

    assert get_run(run["run_id"], cfg)["state"] == "COMPLETED"
    call = list_tool_calls(cfg)["tool_calls"][0]
    assert call["tool_name"] == "sources.search" and call["status"] == "EXECUTED"
    assert call["approved_by"] == agent.AUTO_POLICY and call["requested_by"] == "assistant"
    results = api.requests[1]["messages"][-1]
    assert results["role"] == "user"
    assert results["content"][0]["type"] == "tool_result" and results["content"][0]["tool_use_id"] == "tu_1"
    assert "Searching your documents for “budget”" in progress(run["run_id"], cfg)


def test_source_search_results_are_citable_search_results(cfg, api, chat, tmp_path):
    from backend.app.ingestion import ingest_file

    note = tmp_path / "budget.md"
    note.write_text("The 2026 budget allocates 40 percent to research.", encoding="utf-8")
    ingest_file(note, cfg)
    api.script = [
        message([tool_use("tu_1", "search_sources", {"query": "budget research"})], "tool_use"),
        message([text("Research gets 40 percent.")]),
    ]
    ask(chat, "How much goes to research?", cfg)

    content = api.requests[1]["messages"][-1]["content"][0]["content"]
    assert content[0]["type"] == "search_result" and content[0]["title"] == "budget.md"
    assert content[0]["source"].startswith("mucaro://source/src_")
    assert content[0]["citations"] == {"enabled": True}
    assert "40 percent" in content[0]["content"][0]["text"]


def test_state_changing_tool_waits_for_approval_then_continues(cfg, api, chat):
    api.script = [
        message([tool_use("tu_1", "remember", {"content": "Prefers metric units", "kind": "preference"})], "tool_use"),
        message([text("Saved.")]),
    ]
    run = ask(chat, "Remember that I prefer metric units.", cfg)

    run = get_run(run["run_id"], cfg)
    assert run["state"] == "AWAITING_APPROVAL"
    request = events(run["run_id"], cfg, "approval_request")
    assert request[0]["data"]["tool_name"] == "memory.remember"
    assert list_memory_items(cfg)["memory_count"] == 0

    approve_tool_call(request[0]["data"]["tool_call_id"], cfg=cfg, approved_by="user")
    resume_waiting_run(run["run_id"], cfg)

    assert get_run(run["run_id"], cfg)["state"] == "COMPLETED"
    assert list_memory_items(cfg)["memories"][0]["content"] == "Prefers metric units"
    result = api.requests[1]["messages"][-1]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "tu_1" and "is_error" not in result


def test_declined_action_is_reported_to_claude(cfg, api, chat):
    api.script = [
        message([tool_use("tu_1", "remember", {"content": "x", "kind": "fact"})], "tool_use"),
        message([text("Okay, I won't save it.")]),
    ]
    run = ask(chat, "Remember x", cfg)
    call_id = events(run["run_id"], cfg, "approval_request")[0]["data"]["tool_call_id"]

    deny_tool_call(call_id, cfg=cfg, denied_by="user")
    resume_waiting_run(run["run_id"], cfg)

    result = api.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and "declined" in result["content"]
    assert get_run(run["run_id"], cfg)["state"] == "COMPLETED"


def test_ask_mode_offers_no_server_tools_and_asks_before_reading(cfg, api, chat):
    api.script = [
        message([tool_use("tu_1", "search_sources", {"query": "q"})], "tool_use"),
    ]
    run = ask(chat, "Look it up", cfg, mode="ask")

    names = [tool.get("name") for tool in api.requests[0]["tools"]]
    assert "web_search" not in names and "code_execution" not in names
    assert get_run(run["run_id"], cfg)["state"] == "AWAITING_APPROVAL"


def test_stop_while_waiting_closes_the_open_tool_call(cfg, api, chat):
    api.script = [
        message([text("Saving it."), tool_use("tu_1", "remember", {"content": "x", "kind": "fact"})], "tool_use"),
        message([text("Next answer.")]),
    ]
    run = ask(chat, "Remember x", cfg)
    stop_run(run["run_id"], cfg=cfg)

    assert get_run(run["run_id"], cfg)["state"] == "INTERRUPTED"
    history = load_history(chat, cfg)
    assert agent._open_tool_use_ids(history) == []
    closure = history[-1]["content"][0]
    assert closure["tool_use_id"] == "tu_1" and closure["is_error"] is True and "stopped" in closure["content"]

    second = ask(chat, "Something else", cfg)
    assert get_run(second["run_id"], cfg)["state"] == "COMPLETED"
    roles = [row["role"] for row in api.requests[1]["messages"]]
    assert roles == ["user", "assistant", "user", "user"]


def test_stop_mid_stream_aborts_and_keeps_history_valid(cfg, api, chat, monkeypatch):
    monkeypatch.setattr(agent, "STOP_POLL_SECONDS", 0.0)

    seen = {"deltas": 0}

    def press_stop(event):
        # What the Stop button does to a running run: set the cancel flag the stream loop polls.
        if event["type"] == "content_block_delta" and event["delta"].get("type") == "text_delta":
            seen["deltas"] += 1
        if seen["deltas"] == 3:
            for row in work_runs.list_runs(conversation_id=chat, cfg=cfg)["runs"]:
                if row["state"] == "RUNNING":
                    with work_runs._Tx(cfg) as conn:
                        current = work_runs._load(conn, row["run_id"])
                        current.update(cancel_requested=True, cancel_reason="USER_STOP")
                        work_runs._save(conn, current)

    api.on_line = press_stop
    api.script = [message([text("A long answer that never finishes")])]
    run = ask(chat, "Write a lot", cfg)

    assert get_run(run["run_id"], cfg)["state"] == "INTERRUPTED"
    history = load_history(chat, cfg)
    assert [row["role"] for row in history] == ["user", "assistant"]
    assert history[1]["content"][0]["text"].endswith("[Stopped before finishing.]")


def test_pause_turn_is_resumed_with_the_paused_message(cfg, api, chat):
    paused = [server_tool_use("srv_1", "web_search", {"query": "news"})]
    api.script = [message(paused, "pause_turn"), message([text("Here is the news.")])]
    run = ask(chat, "news?", cfg)

    assert get_run(run["run_id"], cfg)["outcome"] == "ANSWERED"
    resumed = api.requests[1]["messages"]
    assert resumed[-1]["role"] == "assistant" and resumed[-1]["content"][0]["type"] == "server_tool_use"


def test_refusal_and_length_limit_are_reported(cfg, api, chat):
    api.script = [message([text("")], "refusal", stop_details={"type": "refusal", "category": "cyber", "explanation": None})]
    run = ask(chat, "something declined", cfg)
    assert get_run(run["run_id"], cfg)["outcome"] == "DECLINED"
    assert events(run["run_id"], cfg, "warning")[0]["data"]["status"] == "WARN_CLAUDE_DECLINED"

    api.script = [message([text("partial"), tool_use("tu_9", "search_sources", {"query": "cut"})], "max_tokens")]
    run = ask(chat, "long", cfg)
    assert get_run(run["run_id"], cfg)["outcome"] == "ANSWER_TRUNCATED"
    assert agent._open_tool_use_ids(load_history(chat, cfg)) == []


def test_api_errors_fail_the_run_with_a_plain_status(cfg, api, chat):
    anthropic = pytest.importorskip("anthropic")
    import httpx

    response = httpx.Response(401, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
                              json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})
    api.script = [anthropic.AuthenticationError("invalid x-api-key", response=response, body=response.json())]
    run = ask(chat, "hello", cfg)

    run = get_run(run["run_id"], cfg)
    assert run["state"] == "FAILED" and run["error"]["status"] == "ERROR_CLAUDE_KEY_REJECTED"
    assert FAKE_KEY not in json.dumps(list_run_events(run["run_id"], cfg=cfg))
    assert [row["role"] for row in load_history(chat, cfg)] == ["user"]


def test_restart_during_a_tool_round_leaves_a_valid_history(cfg, api, chat):
    api.script = [message([tool_use("tu_1", "remember", {"content": "x", "kind": "fact"})], "tool_use")]
    run = ask(chat, "Remember x", cfg)
    # Simulate the app dying while the run was RUNNING mid-round.
    with work_runs._Tx(cfg) as conn:
        current = work_runs._load(conn, run["run_id"])
        current["state"] = "RUNNING"
        work_runs._save(conn, current)

    reconcile_runs_on_startup(cfg)

    assert get_run(run["run_id"], cfg)["state"] == "INTERRUPTED"
    history = load_history(chat, cfg)
    assert agent._open_tool_use_ids(history) == []
    assert "restarted" in history[-1]["content"][0]["content"]


def test_history_is_replayed_byte_for_byte(cfg, api, chat):
    api.script = [
        message([thinking("Weighing it.", signature="sig-AAA"), text("First.")]),
        message([text("Second.")]),
    ]
    ask(chat, "one", cfg)
    stored = [json.loads(row["content_json"]) for row in load_history_rows(chat, cfg)]
    ask(chat, "two", cfg)

    replayed = api.requests[1]["messages"][: len(stored)]
    assert [row["content"] for row in replayed] == stored
    assert replayed[1]["content"][0] == {"type": "thinking", "thinking": "Weighing it.", "signature": "sig-AAA"}


def test_earlier_offline_turns_are_carried_into_claude_context(cfg, api, chat, monkeypatch):
    monkeypatch.setenv("MUCARO_AI_SECRET_STORE", "env")
    save_app_settings({"live_consent": False}, cfg)
    first = submit_message(chat, "/status", mode="auto", cfg=cfg)["run"]
    assert get_run(first["run_id"], cfg)["state"] == "COMPLETED"
    save_app_settings({"live_consent": True}, cfg)

    api.script = [message([text("Got it.")])]
    ask(chat, "Continue please", cfg)

    sent = api.requests[0]["messages"]
    assert sent[0] == {"role": "user", "content": [{"type": "text", "text": "/status"}]}
    assert sent[-1]["content"][1]["text"] == "Continue please"


def test_conversation_tools_are_frozen_and_deleted_with_the_chat(cfg, api, chat):
    api.script = [message([text("a")]), message([text("b")])]
    ask(chat, "one", cfg)
    save_app_settings({"web_tools": False}, cfg)
    ask(chat, "two", cfg)
    assert api.requests[0]["tools"] == api.requests[1]["tools"]

    delete_conversation(chat, cfg)
    assert load_history(chat, cfg) == []


def test_claude_is_not_used_without_consent(cfg, api, chat):
    save_app_settings({"live_consent": False}, cfg)
    run = ask(chat, "hello", cfg)

    assert api.requests == []
    assert "engine" not in get_run(run["run_id"], cfg)


def test_files_outside_granted_folders_are_refused(cfg, api, chat, tmp_path_factory):
    granted = tmp_path_factory.mktemp("granted")
    (granted / "notes.txt").write_text("hello from a granted folder", encoding="utf-8")
    secret = tmp_path_factory.mktemp("elsewhere") / "secret.txt"
    secret.write_text("nope", encoding="utf-8")
    grant_directory(granted, cfg, granted_by="test")
    api.script = [
        message([tool_use("tu_1", "read_file", {"path": str(granted / "notes.txt")}),
                 tool_use("tu_2", "read_file", {"path": str(secret)})], "tool_use"),
        message([text("Done.")]),
    ]
    ask(chat, "read both", cfg)

    results = api.requests[1]["messages"][-1]["content"]
    assert "hello from a granted folder" in results[0]["content"]
    assert results[1]["is_error"] is True and "ERROR_FILE_PERMISSION_REQUIRED" in results[1]["content"]


def test_invalid_tool_input_is_reported_without_running(cfg, api, chat):
    api.script = [
        message([tool_use("tu_1", "search_sources", {"top_k": 3})], "tool_use"),
        message([text("Sorry.")]),
    ]
    ask(chat, "search", cfg)

    result = api.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and "ERROR_TOOL_INPUT_INVALID" in result["content"]
    assert list_tool_calls(cfg)["tool_calls"] == []


def test_plan_mode_never_calls_claude(cfg, api, chat):
    run = ask(chat, "hello", cfg, mode="plan")
    assert get_run(run["run_id"], cfg)["outcome"] == "PLAN_ONLY"
    assert api.requests == []


def test_secrets_never_appear_in_events_or_history(cfg, api, chat, tmp_path):
    api.script = [message([text("ok")])]
    run = ask(chat, "hi", cfg)
    blob = json.dumps(list_run_events(run["run_id"], cfg=cfg)) + json.dumps(load_history(chat, cfg)) + json.dumps(get_run(run["run_id"], cfg))
    assert FAKE_KEY not in blob
    assert not any(FAKE_KEY in path.read_text(encoding="utf-8", errors="ignore") for path in Path(cfg.data_dir).rglob("*") if path.is_file() and path.suffix != ".sqlite3")


def test_engine_option_selects_the_pipeline(cfg, api, chat):
    cited = submit_message(chat, "hello", mode="agent", options={"engine": "cited"}, cfg=cfg)["run"]
    assert api.requests == [] and "engine" not in get_run(cited["run_id"], cfg)

    save_app_settings({"live_consent": False}, cfg)
    forced = submit_message(chat, "hello", mode="agent", options={"engine": "agent"}, cfg=cfg)["run"]
    failed = get_run(forced["run_id"], cfg)
    assert failed["state"] == "FAILED" and failed["error"]["status"] == "ERROR_CLAUDE_CONSENT_REQUIRED"
    assert api.requests == []
