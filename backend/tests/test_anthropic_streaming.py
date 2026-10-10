"""Anthropic adapter: SDK-free HTTPS/SSE streaming, effort, errors, and work-run delivery."""
from __future__ import annotations

import io
import json
import urllib.error
from types import SimpleNamespace

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


def _event(name: str, payload: dict) -> bytes:
    return f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode()


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False

    def __iter__(self):
        return iter(self.readlines())


@pytest.fixture()
def fake_http(monkeypatch):
    state = SimpleNamespace(
        deltas=["Lineage binds ", "chunks to sources ", "(chunk_id=chunk_abc123)."],
        stop_reason="end_turn",
        error=None,
        calls=[],
    )

    def fake_urlopen(request, timeout):
        state.calls.append(
            {
                "url": request.full_url,
                "headers": dict(request.header_items()),
                "payload": json.loads(request.data.decode()),
                "timeout": timeout,
            }
        )
        body = bytearray()
        body.extend(_event("message_start", {"type": "message_start", "message": {"id": "msg_test"}}))
        for delta in state.deltas:
            body.extend(_event("content_block_delta", {"type": "content_block_delta", "delta": {"type": "text_delta", "text": delta}}))
        if state.error:
            body.extend(_event("error", {"type": "error", "error": state.error}))
        else:
            body.extend(_event("message_delta", {"type": "message_delta", "delta": {"stop_reason": state.stop_reason}}))
            body.extend(_event("message_stop", {"type": "message_stop"}))
        return FakeResponse(bytes(body))

    monkeypatch.setattr(model_router.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("MUCARO_AI_LIVE_MODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("MUCARO_AI_OFFLINE", raising=False)
    monkeypatch.delenv("MUCARO_AI_MODEL_EFFORT", raising=False)
    return state


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
    state.deltas = ["Lineage binds chunks ", f"to sources (chunk_id={chunk_id})."]
    state.stop_reason = stop_reason


def test_tokens_stream_live_over_stdlib_https_with_low_effort_by_default(fake_http):
    got: list[str] = []
    result = generate_answer("How?", CHUNK, model_provider="anthropic", model_name="claude-sonnet-5", on_text_delta=got.append)
    assert got == fake_http.deltas
    assert result["answer"] == "".join(got)
    assert result["streamed"] is True
    assert result["cited_chunk_ids"] == ["chunk_abc123"]
    assert result["stop_reason"] == "end_turn"
    call = fake_http.calls[-1]
    assert call["url"] == model_router.ANTHROPIC_MESSAGES_URL
    assert call["payload"]["stream"] is True
    assert call["payload"]["max_tokens"] == model_router.ANTHROPIC_MAX_OUTPUT_TOKENS
    assert call["payload"]["output_config"] == {"effort": "low"}
    assert call["headers"]["X-api-key"] == "test-key"
    assert call["headers"]["Anthropic-version"] == model_router.ANTHROPIC_API_VERSION


def test_answer_without_listener_is_complete_but_not_marked_streamed(fake_http):
    result = generate_answer("How?", CHUNK, model_provider="anthropic", model_name="claude-sonnet-5")
    assert result["answer"] == "".join(fake_http.deltas)
    assert result["streamed"] is False


@pytest.mark.parametrize(
    ("model", "env_effort", "expected"),
    [
        ("claude-sonnet-5", "medium", {"effort": "medium"}),
        ("claude-sonnet-5", " HIGH ", {"effort": "high"}),
        ("claude-sonnet-5", "default", None),
        ("claude-haiku-4-5", None, None),
    ],
)
def test_effort_follows_setting(fake_http, monkeypatch, model, env_effort, expected):
    if env_effort is not None:
        monkeypatch.setenv("MUCARO_AI_MODEL_EFFORT", env_effort)
    generate_answer("How?", CHUNK, model_provider="anthropic", model_name=model)
    assert fake_http.calls[-1]["payload"].get("output_config") == expected


def test_invalid_effort_is_refused_before_request(fake_http, monkeypatch):
    monkeypatch.setenv("MUCARO_AI_MODEL_EFFORT", "turbo")
    with pytest.raises(ModelProviderError) as exc:
        generate_answer("How?", CHUNK, model_provider="anthropic", model_name="claude-sonnet-5")
    assert exc.value.status == "ERROR_MODEL_EFFORT_INVALID"
    assert fake_http.calls == []


@pytest.mark.parametrize(
    ("error_type", "status"),
    [
        ("rate_limit_error", "ERROR_MODEL_PROVIDER_RATE_LIMIT"),
        ("overloaded_error", "ERROR_MODEL_PROVIDER_UNAVAILABLE"),
        ("api_error", "ERROR_MODEL_PROVIDER_UNAVAILABLE"),
        ("invalid_request_error", "ERROR_MODEL_PROVIDER_REQUEST_REJECTED"),
    ],
)
def test_sse_errors_map_to_provider_statuses(fake_http, error_type, status):
    fake_http.deltas = ["Partial "]
    fake_http.error = {"type": error_type, "message": "provider error"}
    got: list[str] = []
    with pytest.raises(ModelProviderError) as exc:
        generate_answer("How?", CHUNK, model_provider="anthropic", model_name="claude-sonnet-5", on_text_delta=got.append)
    assert exc.value.status == status
    assert got == ["Partial "]


@pytest.mark.parametrize(
    ("code", "status"),
    [(429, "ERROR_MODEL_PROVIDER_RATE_LIMIT"), (408, "ERROR_MODEL_PROVIDER_TIMEOUT"), (504, "ERROR_MODEL_PROVIDER_TIMEOUT"), (500, "ERROR_MODEL_PROVIDER_UNAVAILABLE"), (400, "ERROR_MODEL_PROVIDER_REQUEST_REJECTED")],
)
def test_http_errors_map_to_provider_statuses(fake_http, monkeypatch, code, status):
    def fail(request, timeout):
        raise urllib.error.HTTPError(request.full_url, code, "failure", {}, None)
    monkeypatch.setattr(model_router.urllib.request, "urlopen", fail)
    with pytest.raises(ModelProviderError) as exc:
        generate_answer("How?", CHUNK, model_provider="anthropic", model_name="claude-sonnet-5")
    assert exc.value.status == status


@pytest.mark.parametrize(
    ("stop_reason", "flag"),
    [("max_tokens", "WARN_MODEL_OUTPUT_TRUNCATED"), ("refusal", "WARN_MODEL_REFUSAL"), ("end_turn", None)],
)
def test_stop_reasons_are_recorded_and_flagged(fake_http, cfg, stop_reason, flag):
    _cite_ingested_chunk(fake_http, cfg, stop_reason)
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


def test_trace_times_first_streamed_token_separately(fake_http, cfg):
    _cite_ingested_chunk(fake_http, cfg)
    trace = answer_question("How does source lineage work?", model_provider="anthropic", allow_live=True, cfg=cfg, on_text_delta=lambda text: None)
    timings = trace["timings_ms"]
    assert trace["answer_streamed"] is True
    assert 0 <= timings["model_first_token_ms"] <= timings["model_ms"]


def test_work_run_delivers_anthropic_tokens_live(fake_http, cfg):
    _cite_ingested_chunk(fake_http, cfg)
    work_runs.set_execution_mode("inline")
    try:
        conversation = create_conversation("live", cfg)["conversation_id"]
        run = submit_message(conversation, "How does source lineage work?", options={"model_provider": "anthropic", "allow_live": True}, cfg=cfg)["run"]
    finally:
        work_runs.set_execution_mode("thread")
    events = list_run_events(run["run_id"], cfg=cfg)
    deltas = [event["data"] for event in events if event["type"] == "text_delta"]
    assert [delta["text"] for delta in deltas] == fake_http.deltas
    assert all(delta["live"] for delta in deltas)
    assert get_run(run["run_id"], cfg)["outcome"] == "ANSWERED"


def test_failure_mid_stream_keeps_partial_text_and_fails_run(fake_http, cfg):
    fake_http.deltas = ["Lineage binds "]
    fake_http.error = {"type": "overloaded_error", "message": "Overloaded"}
    work_runs.set_execution_mode("inline")
    try:
        conversation = create_conversation("live", cfg)["conversation_id"]
        run = submit_message(conversation, "How does source lineage work?", options={"model_provider": "anthropic", "allow_live": True}, cfg=cfg)["run"]
    finally:
        work_runs.set_execution_mode("thread")
    events = list_run_events(run["run_id"], cfg=cfg)
    assert [event["data"]["text"] for event in events if event["type"] == "text_delta"] == ["Lineage binds "]
    finished = get_run(run["run_id"], cfg)
    assert finished["state"] == "FAILED"
    assert finished["error"]["status"] == "ERROR_MODEL_PROVIDER_UNAVAILABLE"
