"""Anthropic adapter: live token streaming, effort, client reuse and SDK error mapping.

The SDK is replaced by a fake module with the same surface the adapter uses
(Anthropic().messages.stream(...) -> text_stream / get_final_message, and the
exception hierarchy), so these tests never touch the network.
"""
from __future__ import annotations

import importlib.machinery
import sys
import types
from types import SimpleNamespace
from typing import Self

import pytest

from backend.app import model_router, work_runs
from backend.app.contracts import validate_contract
from backend.app.conversations import create_conversation
from backend.app.error_categories import classify_error
from backend.app.ingestion import get_chunks, ingest_file
from backend.app.model_router import ModelProviderError, generate_answer
from backend.app.services import answer_question
from backend.app.work_runs import get_run, list_run_events, submit_message
from backend.tests.test_api import make_settings

CHUNK = [{"chunk_id": "chunk_abc123", "source_id": "src_abc", "text": "Lineage binds chunks to sources."}]


class APIError(Exception):
    def __init__(self, message: str = "api error", *, body: object = None) -> None:
        super().__init__(message)
        self.message = message
        self.body = body


class APIStatusError(APIError):
    def __init__(self, message: str = "status error", *, status_code: int = 500, body: object = None) -> None:
        super().__init__(message, body=body)
        self.status_code = status_code


class RateLimitError(APIStatusError):
    pass


class APIConnectionError(APIError):
    pass


class APITimeoutError(APIConnectionError):
    pass


class _FakeStream:
    def __init__(self, script: dict) -> None:
        self._pending = list(script["deltas"])
        self._streamed: list[str] = []
        self._stop_reason = script["stop_reason"]

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    @property
    def text_stream(self):
        while self._pending:
            item = self._pending.pop(0)
            if isinstance(item, Exception):
                raise item
            self._streamed.append(item)
            yield item

    def get_final_message(self) -> SimpleNamespace:
        for _ in self.text_stream:  # like the SDK, finishing the message consumes the rest of the stream
            pass
        return SimpleNamespace(
            content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text="".join(self._streamed))],
            stop_reason=self._stop_reason,
        )


@pytest.fixture()
def fake_sdk(monkeypatch):
    sdk = types.ModuleType("anthropic")
    sdk.__spec__ = importlib.machinery.ModuleSpec("anthropic", None)  # find_spec() reads it for readiness
    state = SimpleNamespace(
        script={"deltas": ["Lineage binds ", "chunks to sources ", "(chunk_id=chunk_abc123)."], "stop_reason": "end_turn"},
        calls=[],
        instances=0,
    )

    class Anthropic:
        def __init__(self) -> None:
            state.instances += 1
            self.messages = SimpleNamespace(stream=self._stream)

        def _stream(self, **kwargs: object) -> _FakeStream:
            state.calls.append(kwargs)
            return _FakeStream(state.script)

    for name, value in {
        "Anthropic": Anthropic,
        "APIError": APIError,
        "APIStatusError": APIStatusError,
        "RateLimitError": RateLimitError,
        "APIConnectionError": APIConnectionError,
        "APITimeoutError": APITimeoutError,
    }.items():
        setattr(sdk, name, value)
    monkeypatch.setitem(sys.modules, "anthropic", sdk)
    monkeypatch.setenv("MUCARO_AI_LIVE_MODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("MUCARO_AI_MODEL_EFFORT", raising=False)
    model_router._ANTHROPIC_CLIENT.clear()
    yield state
    model_router._ANTHROPIC_CLIENT.clear()


@pytest.fixture()
def cfg(tmp_path):
    settings = make_settings(tmp_path)
    settings.ensure_dirs()
    note = tmp_path / "lineage.md"
    note.write_text("Source lineage binds every chunk to a SHA-256 source identity.", encoding="utf-8")
    ingest_file(note, settings)
    return settings


def _cite_ingested_chunk(state: SimpleNamespace, cfg, stop_reason: str = "end_turn") -> None:
    chunk_id = get_chunks(cfg)[0]["chunk_id"]
    state.script = {"deltas": ["Lineage binds chunks ", f"to sources (chunk_id={chunk_id})."], "stop_reason": stop_reason}


def test_tokens_stream_live_with_low_effort_by_default(fake_sdk):
    got: list[str] = []
    result = generate_answer("How?", CHUNK, model_provider="anthropic", model_name="claude-sonnet-5", on_text_delta=got.append)
    assert got == ["Lineage binds ", "chunks to sources ", "(chunk_id=chunk_abc123)."]
    assert result["answer"] == "".join(got)
    assert result["streamed"] is True
    assert result["cited_chunk_ids"] == ["chunk_abc123"]
    assert result["stop_reason"] == "end_turn"
    call = fake_sdk.calls[-1]
    assert call["model"] == "claude-sonnet-5"
    assert call["max_tokens"] == model_router.ANTHROPIC_MAX_OUTPUT_TOKENS
    assert call["extra_body"] == {"output_config": {"effort": "low"}}


def test_answer_without_a_listener_is_complete_but_not_marked_streamed(fake_sdk):
    result = generate_answer("How?", CHUNK, model_provider="anthropic", model_name="claude-sonnet-5")
    assert result["answer"] == "Lineage binds chunks to sources (chunk_id=chunk_abc123)."
    assert result["streamed"] is False


@pytest.mark.parametrize(
    ("model", "env_effort", "expected"),
    [
        ("claude-sonnet-5", "medium", {"output_config": {"effort": "medium"}}),
        ("claude-sonnet-5", " HIGH ", {"output_config": {"effort": "high"}}),
        ("claude-sonnet-5", "default", None),
        ("claude-haiku-4-5", None, None),
    ],
)
def test_effort_follows_the_setting_and_is_left_out_for_models_without_it(fake_sdk, monkeypatch, model, env_effort, expected):
    if env_effort is not None:
        monkeypatch.setenv("MUCARO_AI_MODEL_EFFORT", env_effort)
    generate_answer("How?", CHUNK, model_provider="anthropic", model_name=model)
    assert fake_sdk.calls[-1].get("extra_body") == expected


def test_invalid_effort_is_refused_before_any_request(fake_sdk, monkeypatch):
    monkeypatch.setenv("MUCARO_AI_MODEL_EFFORT", "turbo")
    with pytest.raises(ModelProviderError) as exc:
        generate_answer("How?", CHUNK, model_provider="anthropic", model_name="claude-sonnet-5")
    assert exc.value.status == "ERROR_MODEL_EFFORT_INVALID"
    assert fake_sdk.calls == []


def test_client_is_reused_across_questions_and_rebuilt_for_a_new_key(fake_sdk, monkeypatch):
    generate_answer("How?", CHUNK, model_provider="anthropic", model_name="claude-sonnet-5")
    generate_answer("How?", CHUNK, model_provider="anthropic", model_name="claude-sonnet-5")
    assert fake_sdk.instances == 1
    monkeypatch.setenv("ANTHROPIC_API_KEY", "rotated-key")
    generate_answer("How?", CHUNK, model_provider="anthropic", model_name="claude-sonnet-5")
    assert fake_sdk.instances == 2
    assert len(model_router._ANTHROPIC_CLIENT) == 1


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (RateLimitError("slow down", status_code=429), "ERROR_MODEL_PROVIDER_RATE_LIMIT"),
        (APITimeoutError("timed out"), "ERROR_MODEL_PROVIDER_TIMEOUT"),
        (APIConnectionError("connection reset"), "ERROR_MODEL_PROVIDER_UNAVAILABLE"),
        (APIStatusError("internal", status_code=500), "ERROR_MODEL_PROVIDER_UNAVAILABLE"),
        (
            APIStatusError("overloaded", status_code=200, body={"type": "error", "error": {"type": "overloaded_error"}}),
            "ERROR_MODEL_PROVIDER_UNAVAILABLE",
        ),
        (
            APIStatusError("rate limited mid-stream", status_code=200, body={"type": "error", "error": {"type": "rate_limit_error"}}),
            "ERROR_MODEL_PROVIDER_RATE_LIMIT",
        ),
        (APIStatusError("bad request", status_code=400), "ERROR_MODEL_PROVIDER_REQUEST_REJECTED"),
        (APIError("unparseable response"), "ERROR_MODEL_PROVIDER_RESPONSE_INVALID"),
    ],
)
def test_sdk_errors_map_to_provider_statuses(fake_sdk, error, status):
    fake_sdk.script = {"deltas": ["Partial ", error], "stop_reason": "end_turn"}
    got: list[str] = []
    with pytest.raises(ModelProviderError) as exc:
        generate_answer("How?", CHUNK, model_provider="anthropic", model_name="claude-sonnet-5", on_text_delta=got.append)
    assert exc.value.status == status
    assert got == ["Partial "]


@pytest.mark.parametrize(
    ("stop_reason", "flag"),
    [("max_tokens", "WARN_MODEL_OUTPUT_TRUNCATED"), ("refusal", "WARN_MODEL_REFUSAL"), ("end_turn", None)],
)
def test_stop_reasons_are_recorded_and_flagged_in_the_trace(fake_sdk, cfg, stop_reason, flag):
    _cite_ingested_chunk(fake_sdk, cfg, stop_reason)
    trace = answer_question("How does source lineage work?", model_provider="anthropic", allow_live=True, cfg=cfg)
    validate_contract("answer_trace", trace)
    assert trace["model_provider"] == "anthropic"
    assert trace["model_stop_reason"] == stop_reason
    assert trace["model_effort"] == "low"
    assert trace["citation_status"] == "CITATION_OK"
    warnings = {item for item in trace["validation_flags"] if item in {"WARN_MODEL_OUTPUT_TRUNCATED", "WARN_MODEL_REFUSAL"}}
    assert warnings == ({flag} if flag else set())
    if flag:
        assert classify_error(flag) == {"WARN_MODEL_OUTPUT_TRUNCATED": "MODEL_UNAVAILABLE", "WARN_MODEL_REFUSAL": "BLOCKED_ACTION"}[flag]


def test_trace_times_the_first_streamed_token_separately(fake_sdk, cfg):
    _cite_ingested_chunk(fake_sdk, cfg)
    trace = answer_question(
        "How does source lineage work?", model_provider="anthropic", allow_live=True, cfg=cfg, on_text_delta=lambda text: None
    )
    timings = trace["timings_ms"]
    assert trace["answer_streamed"] is True
    assert 0 <= timings["model_first_token_ms"] <= timings["model_ms"]
    assert timings["retrieval_ms"] >= 0


def test_work_run_delivers_anthropic_tokens_live_instead_of_rechunking(fake_sdk, cfg):
    _cite_ingested_chunk(fake_sdk, cfg)
    work_runs.set_execution_mode("inline")
    try:
        conversation = create_conversation("live", cfg)["conversation_id"]
        run = submit_message(
            conversation, "How does source lineage work?", options={"model_provider": "anthropic", "allow_live": True}, cfg=cfg
        )["run"]
    finally:
        work_runs.set_execution_mode("thread")
    events = list_run_events(run["run_id"], cfg=cfg)
    deltas = [event["data"] for event in events if event["type"] == "text_delta"]
    assert [delta["text"] for delta in deltas] == fake_sdk.script["deltas"]
    assert all(delta["live"] for delta in deltas)
    assert get_run(run["run_id"], cfg)["outcome"] == "ANSWERED"
    types_seen = [event["type"] for event in events]
    assert types_seen.index("text_delta") < types_seen.index("citation") < types_seen.index("final")


def test_failure_mid_stream_keeps_the_partial_text_and_fails_the_run(fake_sdk, cfg):
    overloaded = APIStatusError("Overloaded", status_code=200, body={"type": "error", "error": {"type": "overloaded_error"}})
    fake_sdk.script = {"deltas": ["Lineage binds ", overloaded], "stop_reason": "end_turn"}
    work_runs.set_execution_mode("inline")
    try:
        conversation = create_conversation("live", cfg)["conversation_id"]
        run = submit_message(
            conversation, "How does source lineage work?", options={"model_provider": "anthropic", "allow_live": True}, cfg=cfg
        )["run"]
    finally:
        work_runs.set_execution_mode("thread")
    events = list_run_events(run["run_id"], cfg=cfg)
    types_seen = [event["type"] for event in events]
    assert [event["data"]["text"] for event in events if event["type"] == "text_delta"] == ["Lineage binds "]
    assert types_seen.index("text_delta") < types_seen.index("error") < types_seen.index("final")
    finished = get_run(run["run_id"], cfg)
    assert finished["state"] == "FAILED"
    assert finished["error"]["status"] == "ERROR_MODEL_PROVIDER_UNAVAILABLE"
    assert finished["error"]["category"] == "MODEL_UNAVAILABLE"
