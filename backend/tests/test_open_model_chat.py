"""Chat works with any model an OpenAI-compatible server offers, local or remote."""

from __future__ import annotations

import json
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from backend.app import model_router, model_settings, work_runs
from backend.app.contracts import validate_contract
from backend.app.conversations import create_conversation, get_conversation, list_conversations
from backend.app.error_categories import classify_error
from backend.app.ingestion import ingest_file
from backend.app.main import create_app
from backend.app.model_router import ModelProviderError, generate_answer, match_installed_model, model_provider_status
from backend.app.model_routing_policy import plan_model_route
from backend.app.services import answer_question
from backend.app.work_runs import get_run, list_run_events, submit_message
from backend.tests.test_api import make_settings

MODELS = ["qwen2.5:7b", "mistral:latest", "Llama3:latest"]
API_KEY = "sk-test-open-model-key"


class _FakeServer(BaseHTTPRequestHandler):
    """OpenAI-compatible server offering several models, optionally behind a Bearer key."""

    requests: list[dict] = []
    headers_seen: list[dict] = []
    require_key: str | None = None
    models: list[str] = MODELS

    def log_message(self, *args):
        return

    def _authorized(self) -> bool:
        type(self).headers_seen.append({key.lower(): value for key, value in self.headers.items()})
        if type(self).require_key and self.headers.get("Authorization") != f"Bearer {type(self).require_key}":
            self.send_response(401)
            self.end_headers()
            return False
        return True

    def do_GET(self):
        if not self._authorized():
            return
        if self.path != "/v1/models":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({"data": [{"id": name} for name in type(self).models]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self._authorized():
            return
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        type(self).requests.append(payload)
        if payload.get("model") not in type(self).models:
            self.send_response(404)
            self.end_headers()
            return
        user = payload["messages"][-1]["content"]
        if 'chunk_id="' in user:
            chunk_id = user.split('chunk_id="', 1)[1].split('"', 1)[0]
            text = f"Lineage binds chunks to sources (chunk_id={chunk_id})."
        else:
            text = f"Hello from {payload['model']}. How can I help?"
        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for piece in [text[i:i + 9] for i in range(0, len(text), 9)]:
                self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': piece}}]})}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        body = json.dumps({"choices": [{"message": {"role": "assistant", "content": text}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def server(monkeypatch):
    _FakeServer.requests = []
    _FakeServer.headers_seen = []
    _FakeServer.require_key = None
    _FakeServer.models = list(MODELS)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FakeServer)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    for name in (
        "MUCARO_AI_LOCAL_MODEL", "MUCARO_AI_LOCAL_MODEL_URL", "MUCARO_AI_REMOTE_MODEL", "MUCARO_AI_REMOTE_MODEL_URL",
        "MUCARO_AI_REMOTE_MODEL_API_KEY", "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    model_router._PROBE_CACHE.clear()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()
    model_router._PROBE_CACHE.clear()


@pytest.fixture()
def cfg(tmp_path):
    settings = make_settings(tmp_path)
    settings.ensure_dirs()
    note = tmp_path / "lineage.md"
    note.write_text("Source lineage binds every chunk to a SHA-256 source identity.", encoding="utf-8")
    ingest_file(note, settings)
    return settings


def _run(cfg, prompt, options):
    work_runs.set_execution_mode("inline")
    try:
        conversation = create_conversation("open-model", cfg)["conversation_id"]
        run = submit_message(conversation, prompt, options=options, cfg=cfg)["run"]
    finally:
        work_runs.set_execution_mode("thread")
    return get_run(run["run_id"], cfg), list_run_events(run["run_id"], cfg=cfg)


# --- model selection -------------------------------------------------------------------------


def test_installed_model_names_match_despite_case_and_the_implicit_latest_tag():
    assert match_installed_model("mistral", MODELS) == "mistral:latest"
    assert match_installed_model("llama3", MODELS) == "Llama3:latest"
    assert match_installed_model("QWEN2.5:7B", MODELS) == "qwen2.5:7b"
    assert match_installed_model("mistral:latest", ["mistral"]) == "mistral"
    assert match_installed_model("phi4", MODELS) is None
    assert match_installed_model("", MODELS) is None


def test_local_provider_is_ready_with_whatever_model_the_server_has(server, cfg, monkeypatch):
    monkeypatch.setenv("MUCARO_AI_LOCAL_MODEL_URL", server)
    status = model_provider_status("openai_compatible_local", cfg)
    assert status["status"] == "MODEL_PROVIDER_READY"
    assert status["resolved_model"] == "qwen2.5:7b"
    assert "LOCAL_MODEL_AUTO_SELECTED:qwen2.5:7b" in status["warnings"]
    assert status["local_server"]["installed_models"] == MODELS


def test_a_named_model_is_never_swapped_for_another_one(server, cfg, monkeypatch):
    monkeypatch.setenv("MUCARO_AI_LOCAL_MODEL_URL", server)
    monkeypatch.setenv("MUCARO_AI_LOCAL_MODEL", "phi4")
    status = model_provider_status("openai_compatible_local", cfg)
    assert status["status"] == "MODEL_PROVIDER_NOT_READY"
    assert "LOCAL_MODEL_NOT_INSTALLED:phi4" in status["warnings"]
    requested = model_provider_status("openai_compatible_local", cfg, model_name="gemma3")
    assert "LOCAL_MODEL_NOT_INSTALLED:gemma3" in requested["warnings"]


def test_route_is_judged_for_the_requested_model_not_the_configured_default(server, cfg, monkeypatch):
    monkeypatch.setenv("MUCARO_AI_LOCAL_MODEL_URL", server)
    monkeypatch.setenv("MUCARO_AI_LOCAL_MODEL", "phi4")  # configured model is absent
    blocked = plan_model_route(task_type="cited_qa", preferred_provider="openai_compatible_local", cfg=cfg)
    assert blocked["status"] == "MODEL_ROUTE_BLOCKED"
    route = plan_model_route(
        task_type="cited_qa", preferred_provider="openai_compatible_local", preferred_model="mistral", cfg=cfg
    )
    assert route["status"] == "MODEL_ROUTE_READY"
    assert route["selected_model"] == "mistral:latest"
    assert route["failover_order"][0] == {"provider": "openai_compatible_local", "model": "mistral:latest"}


def test_saved_settings_override_the_environment_and_persist(server, cfg, monkeypatch):
    monkeypatch.setenv("MUCARO_AI_LOCAL_MODEL_URL", "http://127.0.0.1:9/v1")
    saved = model_settings.save_model_settings({"local": {"base_url": server, "model": "mistral"}}, cfg)
    assert saved["local"] == {"base_url": server, "model": "mistral"}
    status = model_provider_status("openai_compatible_local", cfg)
    assert status["status"] == "MODEL_PROVIDER_READY"
    assert status["resolved_model"] == "mistral:latest"
    cleared = model_settings.save_model_settings({"local": {"model": ""}}, cfg)
    assert cleared["local"]["model"] == ""
    assert cleared["local"]["base_url"] == server


def test_base_url_normalisation_and_validation():
    assert model_settings.normalize_base_url("localhost:1234") == "http://localhost:1234/v1"
    assert model_settings.normalize_base_url("http://192.168.1.20:8000/") == "http://192.168.1.20:8000/v1"
    assert model_settings.normalize_base_url("https://models.example.com/openai/v1/") == "https://models.example.com/openai/v1"
    for bad in ("", "ftp://host/v1", "http://user:pw@host/v1", "http://host:notaport/v1"):
        with pytest.raises(model_settings.ModelSettingsError):
            model_settings.normalize_base_url(bad)


# --- chat turns ------------------------------------------------------------------------------


def test_greeting_gets_a_reply_from_the_chosen_model_marked_as_using_no_sources(server, cfg, monkeypatch):
    monkeypatch.setenv("MUCARO_AI_LOCAL_MODEL_URL", server)
    run, events = _run(cfg, "hi", {"model_provider": "openai_compatible_local", "model_name": "mistral"})
    assert run["state"] == "COMPLETED"
    assert run["outcome"] == "ANSWERED"
    trace = run["answer_trace"] if "answer_trace" in run else None
    text = "".join(event["data"]["text"] for event in events if event["type"] == "text_delta")
    assert text == "Hello from mistral:latest. How can I help?"
    warnings = [event["data"]["status"] for event in events if event["type"] == "warning"]
    assert "WARN_NO_SOURCES_USED" in warnings
    assert not any(status.startswith("ERROR_") for status in warnings)
    retrieval = next(event["data"] for event in events if event["type"] == "retrieval")
    assert retrieval["grounding"] == "NO_SOURCES_USED"
    assert retrieval["retrieved_chunk_ids"] == []
    assert not [event for event in events if event["type"] == "citation"]
    sent = _FakeServer.requests[-1]
    assert sent["model"] == "mistral:latest"
    assert "<source" not in sent["messages"][-1]["content"]
    assert "do not cite" in sent["messages"][0]["content"].lower()
    assert trace is None or trace["citation_status"] == "NO_SOURCES_USED"
    route = next(event["data"] for event in events if event["type"] == "route")
    assert route["selected_provider"] == "openai_compatible_local"
    assert route["selected_model"] == "mistral:latest"


def test_empty_workspace_chat_still_replies(server, tmp_path, monkeypatch):
    monkeypatch.setenv("MUCARO_AI_LOCAL_MODEL_URL", server)
    empty = make_settings(tmp_path)
    empty.ensure_dirs()
    run, events = _run(empty, "hello there", {"model_provider": "openai_compatible_local"})
    assert run["outcome"] == "ANSWERED"
    assert _FakeServer.requests[-1]["model"] == "qwen2.5:7b"  # auto-selected


def test_question_matching_a_source_is_still_answered_with_citations(server, cfg, monkeypatch):
    monkeypatch.setenv("MUCARO_AI_LOCAL_MODEL_URL", server)
    run, events = _run(cfg, "How does source lineage work?", {"model_provider": "openai_compatible_local", "model_name": "llama3"})
    assert run["outcome"] == "ANSWERED"
    assert [event for event in events if event["type"] == "citation"]
    warnings = [event["data"]["status"] for event in events if event["type"] == "warning"]
    assert "WARN_NO_SOURCES_USED" not in warnings
    assert _FakeServer.requests[-1]["model"] == "Llama3:latest"


def test_ungrounded_trace_is_valid_and_never_reported_as_cited(server, cfg, monkeypatch):
    monkeypatch.setenv("MUCARO_AI_LOCAL_MODEL_URL", server)
    trace = answer_question("hi", model_provider="openai_compatible_local", allow_ungrounded=True, cfg=cfg)
    validate_contract("answer_trace", trace)
    assert trace["citation_status"] == "NO_SOURCES_USED"
    assert trace["grounding"] == "NO_SOURCES_USED"
    assert trace["validation_flags"] == ["WARN_NO_SOURCES_USED"]
    assert trace["cited_chunk_ids"] == [] and trace["source_ids"] == []
    assert trace["confidence"] == 0.0
    assert trace["prompt_template_id"] == "conversation_no_sources_v1"


def test_strict_answering_is_unchanged_without_the_chat_opt_in(server, cfg, monkeypatch):
    monkeypatch.setenv("MUCARO_AI_LOCAL_MODEL_URL", server)
    strict = answer_question("hi", model_provider="openai_compatible_local", cfg=cfg)
    assert strict["status"] == "ERROR_NO_RETRIEVAL_CONTEXT"
    assert "answer" not in strict
    assert _FakeServer.requests == []


def test_mock_provider_answers_a_greeting_with_a_plain_notice(cfg):
    run, events = _run(cfg, "hi", {"model_provider": "mock"})
    assert run["outcome"] == "ANSWERED"
    text = "".join(event["data"]["text"] for event in events if event["type"] == "text_delta")
    assert text.startswith("No local source matches this message")
    assert "ERROR_" not in text


def test_unreachable_server_fails_with_a_model_category_and_a_readable_reason(cfg, monkeypatch):
    monkeypatch.setenv("MUCARO_AI_LOCAL_MODEL_URL", "http://127.0.0.1:9/v1")
    monkeypatch.delenv("MUCARO_AI_LOCAL_MODEL", raising=False)
    model_router._PROBE_CACHE.clear()
    run, events = _run(cfg, "hi", {"model_provider": "openai_compatible_local"})
    assert run["state"] == "FAILED"
    error = run["error"]
    assert error["status"] == "ERROR_MODEL_ROUTE_BLOCKED"
    assert error["category"] == "MODEL_UNAVAILABLE"
    assert "no model server is answering at http://127.0.0.1:9/v1" in error["detail"]
    assert error["detail"] != error["status"]
    route = next(event["data"] for event in events if event["type"] == "route")
    assert route["status"] == "MODEL_ROUTE_BLOCKED"
    assert "http://127.0.0.1:9/v1" in route["detail"]


def test_error_categories_for_no_match_and_blocked_route():
    assert classify_error("ERROR_NO_RETRIEVAL_CONTEXT") == "SOURCE_UNAVAILABLE"
    assert classify_error("ERROR_MODEL_ROUTE_BLOCKED") == "MODEL_UNAVAILABLE"
    assert classify_error("ERROR_REMOTE_MODEL_UNAVAILABLE") == "MODEL_UNAVAILABLE"
    assert classify_error("ERROR_SOMETHING_NEW") == "INTERNAL_FAILURE"


# --- remote servers --------------------------------------------------------------------------


def test_remote_provider_is_not_ready_until_a_server_is_configured(cfg, monkeypatch):
    for name in ("MUCARO_AI_REMOTE_MODEL_URL", "MUCARO_AI_REMOTE_MODEL", "MUCARO_AI_REMOTE_MODEL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    status = model_provider_status("openai_compatible_remote", cfg)
    assert status["status"] == "MODEL_PROVIDER_NOT_READY"
    assert status["warnings"] == ["REMOTE_MODEL_URL_NOT_SET"]
    assert status["live"] is True and status["locality"] == "remote"
    with pytest.raises(ModelProviderError) as exc:
        generate_answer("q", [], model_provider="openai_compatible_remote", cfg=cfg)
    assert exc.value.status == "ERROR_REMOTE_MODEL_URL_NOT_SET"


def test_remote_provider_needs_explicit_consent_per_chat(server, cfg):
    model_settings.save_model_settings({"remote": {"base_url": server, "model": "mistral"}}, cfg)
    blocked = plan_model_route(task_type="cited_qa", preferred_provider="openai_compatible_remote", cfg=cfg)
    assert blocked["status"] == "MODEL_ROUTE_BLOCKED"
    row = next(item for item in blocked["candidates"] if item["provider"] == "openai_compatible_remote")
    assert row["reasons"] == ["LIVE_PROVIDER_NOT_EXPLICITLY_ALLOWED"]
    default = plan_model_route(task_type="cited_qa", allow_live=True, cfg=cfg)
    assert default["selected_provider"] != "openai_compatible_remote"  # never chosen implicitly
    run, _events = _run(cfg, "hi", {"model_provider": "openai_compatible_remote"})
    assert run["state"] == "FAILED"
    assert "has not been allowed for this chat" in run["error"]["detail"]
    assert _FakeServer.requests == []


def test_remote_chat_sends_the_key_and_the_key_is_never_returned(server, cfg):
    _FakeServer.require_key = API_KEY
    client = TestClient(create_app(cfg))
    wrong = client.put("/models/settings", json={"remote": {"base_url": server, "model": "qwen2.5:7b", "api_key": "wrong"}})
    assert wrong.status_code == 200
    rejected = next(row for row in wrong.json()["providers"] if row["name"] == "openai_compatible_remote")
    assert "REMOTE_MODEL_SERVER_REJECTED_KEY" in rejected["warnings"]
    saved = client.put("/models/settings", json={"remote": {"api_key": API_KEY}})
    assert saved.status_code == 200
    assert saved.json()["settings"]["remote"] == {"base_url": server, "model": "qwen2.5:7b", "api_key_configured": True}
    listing = client.get("/models")
    assert API_KEY not in saved.text and API_KEY not in listing.text
    ready = next(row for row in listing.json()["providers"] if row["name"] == "openai_compatible_remote")
    assert ready["status"] == "MODEL_PROVIDER_READY"
    assert ready["local_server"]["api_key_configured"] is True
    assert stat.S_IMODE((cfg.processed_dir / model_settings.SETTINGS_FILENAME).stat().st_mode) == 0o600

    run, events = _run(cfg, "hi", {"model_provider": "openai_compatible_remote", "allow_live": True})
    assert run["outcome"] == "ANSWERED"
    assert _FakeServer.headers_seen[-1]["authorization"] == f"Bearer {API_KEY}"
    assert API_KEY not in json.dumps(events) and API_KEY not in json.dumps(run)
    trace = answer_question(
        "How does source lineage work?", model_provider="openai_compatible_remote", allow_live=True, cfg=cfg
    )
    assert trace["model_provider"] == "openai_compatible_remote"
    assert trace["citation_status"] == "CITATION_OK"
    validate_contract("answer_trace", trace)


def test_local_provider_still_refuses_servers_on_other_machines(cfg):
    model_settings.save_model_settings({"local": {"base_url": "http://192.168.1.20:11434/v1"}}, cfg)
    status = model_provider_status("openai_compatible_local", cfg)
    assert "LOCAL_MODEL_URL_NOT_LOOPBACK" in status["warnings"]
    with pytest.raises(ModelProviderError) as exc:
        generate_answer("q", [], model_provider="openai_compatible_local", cfg=cfg)
    assert exc.value.status == "ERROR_LOCAL_MODEL_URL_NOT_LOOPBACK"


# --- API -------------------------------------------------------------------------------------


def test_probe_endpoint_lists_models_for_the_picker(server, cfg):
    client = TestClient(create_app(cfg))
    found = client.post("/models/probe", json={"base_url": server.removesuffix("/v1")})
    assert found.status_code == 200
    assert found.json()["models"] == MODELS
    assert found.json()["base_url"] == server
    assert found.json()["locality"] == "local"
    down = client.post("/models/probe", json={"base_url": "http://127.0.0.1:9"})
    assert down.json()["status"] == "MODEL_SERVER_UNREACHABLE"
    bad = client.post("/models/probe", json={"base_url": "ftp://nope"})
    assert bad.status_code == 400
    assert bad.json()["detail"]["status"] == "ERROR_MODEL_SETTINGS_URL_INVALID"


def test_conversation_remembers_the_model_it_was_started_with(server, cfg):
    client = TestClient(create_app(cfg))
    created = client.post(
        "/conversations",
        json={"title": "open", "model": {"model_provider": "openai_compatible_local", "model_name": "mistral:latest"}},
    )
    assert created.status_code == 200
    expected = {"model_provider": "openai_compatible_local", "model_name": "mistral:latest", "allow_live": False}
    assert created.json()["model"] == expected
    conversation_id = created.json()["conversation_id"]
    assert get_conversation(conversation_id, cfg)["model"] == expected
    assert next(row for row in list_conversations(cfg) if row["conversation_id"] == conversation_id)["model"] == expected
    unknown = client.post("/conversations", json={"title": "x", "model": {"model_provider": "nope"}})
    assert unknown.status_code == 400
    plain = client.post("/conversations", json={"title": "plain"})
    assert "model" not in plain.json()
