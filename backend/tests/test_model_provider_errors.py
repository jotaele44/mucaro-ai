from __future__ import annotations

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
        (_exc("SomethingElse", status_code=429), "ERROR_MODEL_PROVIDER_RATE_LIMIT"),
        (_exc("APITimeoutError"), "ERROR_MODEL_PROVIDER_TIMEOUT"),
        (TimeoutError("slow"), "ERROR_MODEL_PROVIDER_TIMEOUT"),
        (_exc("APIConnectionError"), "ERROR_MODEL_PROVIDER_UNAVAILABLE"),
        (_exc("APIStatusError", status_code=529), "ERROR_MODEL_PROVIDER_UNAVAILABLE"),
        (_exc("InternalServerError", status_code=500), "ERROR_MODEL_PROVIDER_UNAVAILABLE"),
        (_exc("BadRequestError", status_code=400), "ERROR_MODEL_PROVIDER_CALL_FAILED"),
        (_exc("AuthenticationError", status_code=401), "ERROR_MODEL_PROVIDER_CALL_FAILED"),
        (ValueError("unknown"), "ERROR_MODEL_PROVIDER_CALL_FAILED"),
    ],
)
def test_classification_maps_sdk_errors_to_stable_statuses(exc: Exception, status: str) -> None:
    assert classify_provider_exception(exc).status == status


def test_provider_error_passes_through_unchanged() -> None:
    original = ModelProviderError("ERROR_LIVE_MODE_DISABLED", "x")
    assert classify_provider_exception(original) is original


def test_classified_statuses_are_exactly_what_recovery_pauses_on() -> None:
    from backend.app.provider_recovery import PROVIDER_INTERRUPTION_STATUSES

    for name in ("RateLimitError", "APITimeoutError", "APIConnectionError"):
        assert classify_provider_exception(_exc(name)).status in PROVIDER_INTERRUPTION_STATUSES
    assert classify_provider_exception(_exc("BadRequestError", status_code=400)).status not in PROVIDER_INTERRUPTION_STATUSES


class _FakeClient:
    def __init__(self, behavior):
        self.messages = self
        self._behavior = behavior

    def create(self, **kwargs):
        return self._behavior()


def _install_fake_anthropic(monkeypatch: pytest.MonkeyPatch, behavior) -> None:
    import sys
    import types

    module = types.ModuleType("anthropic")
    module.Anthropic = lambda: _FakeClient(behavior)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", module)
    monkeypatch.setenv("MUCARO_AI_LIVE_MODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


CHUNKS = [{"chunk_id": "c1", "source_id": "s1", "text": "alpha"}]


def test_live_call_rate_limit_becomes_interruption_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def behavior():
        raise _exc("RateLimitError", "429 slow down")

    _install_fake_anthropic(monkeypatch, behavior)
    with pytest.raises(ModelProviderError) as exc:
        generate_answer("q", CHUNKS, model_provider="anthropic")
    assert exc.value.status == "ERROR_MODEL_PROVIDER_RATE_LIMIT"


def test_live_call_empty_content_is_malformed_not_a_blank_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        content: list = []

    _install_fake_anthropic(monkeypatch, lambda: Response())
    with pytest.raises(ModelProviderError) as exc:
        generate_answer("q", CHUNKS, model_provider="anthropic")
    assert exc.value.status == "ERROR_MODEL_PROVIDER_MALFORMED_RESPONSE"


def test_live_call_success_returns_cited_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    class Block:
        type = "text"
        text = "Answer per c1."

    class Response:
        content = [Block()]

    _install_fake_anthropic(monkeypatch, lambda: Response())
    result = generate_answer("q", CHUNKS, model_provider="anthropic")
    assert result["cited_chunk_ids"] == ["c1"]


def test_live_call_is_denied_by_permission_engine_before_reaching_the_sdk(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from dataclasses import replace

    from backend.app.config import settings

    def behavior():
        raise AssertionError("SDK must not be reached")

    _install_fake_anthropic(monkeypatch, behavior)
    with pytest.raises(ModelProviderError) as exc:
        generate_answer("q", CHUNKS, model_provider="anthropic", cfg=replace(settings, distribution_channel="sideload"))
    assert exc.value.status == "ERROR_PERMISSION_DENIED"


def test_mock_provider_needs_no_permission_and_never_calls_the_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_router, "require", lambda *a, **k: (_ for _ in ()).throw(AssertionError("engine called")))
    assert generate_answer("q", CHUNKS, model_provider="mock")["model_provider"] == "mock"
