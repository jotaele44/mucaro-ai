import io
import json

import pytest

from backend.app.model_router import ModelProviderError, generate_answer, list_model_providers, model_provider_status


def test_mock_model_cites_chunks():
    chunks = [{"chunk_id": "chunk_a", "source_id": "src_a", "text": "Source lineage requires citations."}]
    answer = generate_answer("What is required?", chunks, model_provider="mock")
    assert answer["model_provider"] == "mock"
    assert answer["cited_chunk_ids"] == ["chunk_a"]


def test_default_model_is_mock_even_when_api_key_exists(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    chunks = [{"chunk_id": "chunk_a", "source_id": "src_a", "text": "Source lineage requires citations."}]

    answer = generate_answer("What is required?", chunks)

    assert answer["model_provider"] == "mock"


def test_model_provider_registry_reports_mock_ready():
    providers = {provider["name"]: provider for provider in list_model_providers()}

    assert set(providers) == {"mock", "anthropic", "openai_compatible_local"}
    assert providers["mock"]["status"] == "MODEL_PROVIDER_READY"
    assert providers["mock"]["default_model"] == "mock-cited-synthesizer-v1"
    assert providers["mock"]["warnings"] == []


def test_anthropic_provider_status_is_disabled_without_live_mode(monkeypatch):
    monkeypatch.delenv("MUCARO_AI_LIVE_MODE", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    status = model_provider_status(" Anthropic ")

    assert status["name"] == "anthropic"
    assert status["status"] == "MODEL_PROVIDER_DISABLED"
    assert status["requires_live_mode"] is True
    assert status["warnings"] == ["LIVE_MODE_DISABLED"]


def test_anthropic_provider_status_requires_api_key_in_live_mode(monkeypatch):
    monkeypatch.setenv("MUCARO_AI_LIVE_MODE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    status = model_provider_status("anthropic")

    assert status["status"] == "MODEL_PROVIDER_NOT_READY"
    assert status["warnings"] == ["API_KEY_MISSING:ANTHROPIC_API_KEY"]


def test_anthropic_requires_live_mode(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("MUCARO_AI_LIVE_MODE", raising=False)

    with pytest.raises(ModelProviderError) as exc:
        generate_answer("What is required?", [], model_provider="anthropic")

    assert exc.value.status == "ERROR_LIVE_MODE_DISABLED"


def test_anthropic_requires_api_key_when_live_mode_enabled(monkeypatch):
    monkeypatch.setenv("MUCARO_AI_LIVE_MODE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(ModelProviderError) as exc:
        generate_answer("What is required?", [], model_provider="anthropic")

    assert exc.value.status == "ERROR_MODEL_API_KEY_MISSING"


def test_global_offline_mode_disables_remote_provider_and_direct_invocation(monkeypatch):
    monkeypatch.setenv("MUCARO_AI_LIVE_MODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("MUCARO_AI_OFFLINE", "1")

    status = model_provider_status("anthropic")
    assert status["status"] == "MODEL_PROVIDER_DISABLED"
    assert status["system_offline"] is True
    assert "SYSTEM_OFFLINE" in status["warnings"]

    with pytest.raises(ModelProviderError) as exc:
        generate_answer(
            "What is required?",
            [{"chunk_id": "chunk_a", "source_id": "src_a", "text": "Source lineage requires citations."}],
            model_provider="anthropic",
        )
    assert exc.value.status == "ERROR_OFFLINE_NETWORK_DISABLED"


def test_anthropic_provider_status_needs_no_sdk_when_live_and_key_present(monkeypatch):
    monkeypatch.setenv("MUCARO_AI_LIVE_MODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    status = model_provider_status("anthropic")

    assert status["status"] == "MODEL_PROVIDER_READY"
    assert status["optional_dependency"] is None
    assert status["dependency_available"] is None
    assert status["warnings"] == []


def test_anthropic_uses_direct_messages_http_adapter(monkeypatch):
    monkeypatch.setenv("MUCARO_AI_LIVE_MODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    chunks = [{"chunk_id": "chunk_a", "source_id": "src_a", "text": "Source lineage requires citations."}]
    observed = {}

    class FakeResponse(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()
            return False

    def fake_urlopen(request, timeout):
        observed["url"] = request.full_url
        observed["headers"] = dict(request.header_items())
        observed["timeout"] = timeout
        observed["payload"] = json.loads(request.data.decode("utf-8"))
        text = "Source lineage requires citations. (chunk_id=chunk_a)"
        stream = (
            'event: content_block_delta\n'
            f'data: {json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}})}\n\n'
            'event: message_delta\n'
            f'data: {json.dumps({"type": "message_delta", "delta": {"stop_reason": "end_turn"}})}\n\n'
        )
        return FakeResponse(stream.encode("utf-8"))

    monkeypatch.setattr("backend.app.model_router.urllib.request.urlopen", fake_urlopen)

    answer = generate_answer("What is required?", chunks, model_provider="anthropic")

    assert observed["url"] == "https://api.anthropic.com/v1/messages"
    assert observed["headers"]["X-api-key"] == "test-key"
    assert observed["headers"]["Anthropic-version"] == "2023-06-01"
    assert observed["payload"]["messages"][0]["role"] == "user"
    assert observed["payload"]["stream"] is True
    assert answer["model_provider"] == "anthropic"
    assert answer["cited_chunk_ids"] == ["chunk_a"]


def test_unsupported_provider_raises_structured_error():
    with pytest.raises(ModelProviderError) as exc:
        generate_answer("What is required?", [], model_provider="other")

    assert exc.value.status == "ERROR_UNSUPPORTED_MODEL_PROVIDER"
