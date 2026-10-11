from __future__ import annotations

import importlib.machinery
import sys
import types

import pytest

from backend.app import model_router
from backend.app.model_router import ModelProviderError, classify_provider_exception, generate_answer


def _exc(name: str, message: str = "boom", status_code: int | None = None) -> Exception:
    cls = type(name, (Exception,), {})
    error = cls(message)
    if status_code is not None:
        error.status_code = status_code  # type: ignore[attr-defined]
    return error


@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (_exc("RateLimitError"), "ERROR_MODEL_PROVIDER_RATE_LIMIT"),
        (_exc("Anything", status_code=429), "ERROR_MODEL_PROVIDER_RATE_LIMIT"),
        (_exc("APITimeoutError"), "ERROR_MODEL_PROVIDER_TIMEOUT"),
        (TimeoutError("slow"), "ERROR_MODEL_PROVIDER_TIMEOUT"),
        (_exc("APIConnectionError"), "ERROR_MODEL_PROVIDER_UNAVAILABLE"),
        (_exc("APIStatusError", status_code=529), "ERROR_MODEL_PROVIDER_UNAVAILABLE"),
        (_exc("BadRequestError", status_code=400), "ERROR_MODEL_PROVIDER_CALL_FAILED"),
        (_exc("AuthenticationError", status_code=401), "ERROR_MODEL_PROVIDER_CALL_FAILED"),
    ],
)
def test_provider_exception_classification(exc: Exception, status: str) -> None:
    assert classify_provider_exception(exc).status == status


def test_provider_error_passes_through_unchanged() -> None:
    original = ModelProviderError("ERROR_LIVE_MODE_DISABLED", "x")
    assert classify_provider_exception(original) is original


def test_provider_exception_detail_does_not_copy_secret_bearing_message() -> None:
    classified = classify_provider_exception(_exc("RateLimitError", "secret prompt contents"))
    assert "secret prompt contents" not in classified.detail


class _FakeStream:
    """The streaming surface the adapter uses: a context manager with text_stream and get_final_message."""

    def __init__(self, behavior):
        self._behavior = behavior

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        return iter(())

    def get_final_message(self):
        return self._behavior()


class _FakeClient:
    def __init__(self, behavior):
        self.messages = self
        self._behavior = behavior

    def stream(self, **kwargs):
        return _FakeStream(self._behavior)


def _install_fake_anthropic(monkeypatch: pytest.MonkeyPatch, behavior) -> None:
    module = types.ModuleType("anthropic")
    module.__spec__ = importlib.machinery.ModuleSpec("anthropic", None)  # readiness reads it via find_spec()
    module.Anthropic = lambda: _FakeClient(behavior)  # type: ignore[attr-defined]
    module.APIError = type("APIError", (Exception,), {})  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", module)
    monkeypatch.setattr(model_router, "_ANTHROPIC_CLIENT", {})  # never reuse a client cached by another test
    monkeypatch.setenv("MUCARO_AI_LIVE_MODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


CHUNKS = [{"chunk_id": "c1", "source_id": "s1", "text": "alpha"}]


def test_live_rate_limit_becomes_stable_status(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_anthropic(monkeypatch, lambda: (_ for _ in ()).throw(_exc("RateLimitError", "429")))
    with pytest.raises(ModelProviderError) as exc:
        generate_answer("q", CHUNKS, model_provider="anthropic")
    assert exc.value.status == "ERROR_MODEL_PROVIDER_RATE_LIMIT"


def test_live_empty_content_is_malformed_not_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        content: list = []

    _install_fake_anthropic(monkeypatch, Response)
    with pytest.raises(ModelProviderError) as exc:
        generate_answer("q", CHUNKS, model_provider="anthropic")
    assert exc.value.status == "ERROR_MODEL_PROVIDER_MALFORMED_RESPONSE"
