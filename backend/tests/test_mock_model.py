import builtins

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


def test_anthropic_missing_sdk_can_explicitly_fallback_to_mock(monkeypatch):
    monkeypatch.setenv("MUCARO_AI_LIVE_MODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "anthropic":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    chunks = [{"chunk_id": "chunk_a", "source_id": "src_a", "text": "Source lineage requires citations."}]

    answer = generate_answer("What is required?", chunks, model_provider="anthropic", allow_mock_fallback=True)

    assert answer["model_provider"] == "mock"
    assert answer["provider_warning"] == "ERROR_MODEL_SDK_MISSING"


def test_unsupported_provider_raises_structured_error():
    with pytest.raises(ModelProviderError) as exc:
        generate_answer("What is required?", [], model_provider="other")

    assert exc.value.status == "ERROR_UNSUPPORTED_MODEL_PROVIDER"
