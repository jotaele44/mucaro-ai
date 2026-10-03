"""The coding agent's Anthropic path: the live-mode gate, request shape, reply handling and typed-error mapping (S6).

Everything runs offline: the SDK's real exception classes are used to prove the mapping, and a fake client stands
in for the network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from backend.app import coding_agent
from backend.app.cli import main
from backend.app.coding_agent import ANTHROPIC_MAX_TOKENS, PROVIDER_DETAIL_CHARS, provider_chat
from backend.app.model_router import MODEL_PROVIDER_SPECS, ModelProviderError
from backend.app.tools import list_tool_audit_events
from backend.tests.test_coding_agent_hardening import granted_repo

anthropic = pytest.importorskip("anthropic")
httpx = pytest.importorskip("httpx")

KEY = "sk-ant-test-0123456789-NOT-A-REAL-KEY"
MESSAGES = [{"role": "system", "content": "SYSTEM PROMPT"}, {"role": "user", "content": "do the thing"}]


def text_reply(text: str = '{"final": "ok"}', *, stop_reason: str = "end_turn", **extra: Any) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking="private reasoning"), SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        **extra,
    )


class FakeClient:
    """Records how it is built and called; `behaviour` decides what `messages.create` does."""

    constructed: ClassVar[int] = 0
    calls: ClassVar[list[dict[str, Any]]] = []
    behaviour: ClassVar[Any] = staticmethod(lambda **kwargs: text_reply())

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        type(self).constructed += 1
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        type(self).calls.append(kwargs)
        return type(self).behaviour(**kwargs)


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> type[FakeClient]:
    """Live mode on, a key present, and the SDK's client replaced; the SDK's exception classes stay real."""
    FakeClient.constructed, FakeClient.calls, FakeClient.behaviour = 0, [], staticmethod(lambda **kwargs: text_reply())
    monkeypatch.setenv("MUCARO_AI_LIVE_MODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.delenv("MUCARO_AI_MODEL", raising=False)
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    return FakeClient


def status_error(cls: type, code: int, message: str = "boom") -> Exception:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(message, response=httpx.Response(code, request=request), body=None)


# --- the live-mode gate --------------------------------------------------------------------------------------------


def test_the_live_mode_and_key_checks_come_before_the_sdk_is_touched(live: type[FakeClient], monkeypatch: pytest.MonkeyPatch) -> None:
    for value in (None, "0", "", "true", "yes"):  # exactly "1", as `mucaro ask` requires
        if value is None:
            monkeypatch.delenv("MUCARO_AI_LIVE_MODE")
        else:
            monkeypatch.setenv("MUCARO_AI_LIVE_MODE", value)
        with pytest.raises(ModelProviderError) as info:
            provider_chat("anthropic", "some-model")
        assert info.value.status == "ERROR_LIVE_MODE_DISABLED" and "MUCARO_AI_LIVE_MODE=1" in info.value.detail

    monkeypatch.setenv("MUCARO_AI_LIVE_MODE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(ModelProviderError) as info:
        provider_chat("anthropic", "some-model")
    assert info.value.status == "ERROR_MODEL_API_KEY_MISSING" and "ANTHROPIC_API_KEY" in info.value.detail

    monkeypatch.setenv("ANTHROPIC_API_KEY", "")  # empty is absent: an empty value never counts
    with pytest.raises(ModelProviderError) as info:
        provider_chat("anthropic", "some-model")
    assert info.value.status == "ERROR_MODEL_API_KEY_MISSING"
    assert live.constructed == 0 and live.calls == []  # no client was ever built, so nothing could be sent


def test_a_missing_sdk_is_reported_not_raised(live: type[FakeClient], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "anthropic", None)  # `import anthropic` now raises ImportError
    with pytest.raises(ModelProviderError) as info:
        provider_chat("anthropic", "some-model")
    assert info.value.status == "ERROR_MODEL_SDK_MISSING"


def test_the_cli_refuses_a_live_provider_without_the_opt_in(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], live: type[FakeClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, repo = granted_repo(tmp_path)
    monkeypatch.delenv("MUCARO_AI_LIVE_MODE")

    assert main(["code", "agent", "look around", "--repo", str(repo), "--provider", "anthropic", "--model", "m", "--yes"], cfg) == 2

    error = json.loads(capsys.readouterr().err)
    assert error["status"] == "ERROR_LIVE_MODE_DISABLED"
    assert live.constructed == 0 and live.calls == []  # repository content was not sent anywhere


# --- request and reply ------------------------------------------------------------------------------------------


def test_the_request_is_minimal_and_only_text_blocks_become_the_reply(live: type[FakeClient]) -> None:
    chat = provider_chat("anthropic", "claude-test-1")

    assert chat(MESSAGES) == '{"final": "ok"}'  # the thinking block is not part of the reply

    (call,) = live.calls
    assert call == {"model": "claude-test-1", "max_tokens": ANTHROPIC_MAX_TOKENS, "system": "SYSTEM PROMPT", "messages": [MESSAGES[1]]}
    assert ANTHROPIC_MAX_TOKENS >= 16_000  # room for always-thinking models to think and then answer


def test_the_model_comes_from_the_argument_then_the_environment_then_the_provider_default(live: type[FakeClient], monkeypatch: pytest.MonkeyPatch) -> None:
    provider_chat("anthropic")(MESSAGES)
    assert live.calls[-1]["model"] == MODEL_PROVIDER_SPECS["anthropic"].default_model
    monkeypatch.setenv("MUCARO_AI_MODEL", "from-env")
    provider_chat("anthropic")(MESSAGES)
    assert live.calls[-1]["model"] == "from-env"
    provider_chat("anthropic", "from-argument")(MESSAGES)
    assert live.calls[-1]["model"] == "from-argument"


@pytest.mark.parametrize(
    ("reply", "status", "mentions"),
    [
        (text_reply("", stop_reason="refusal", stop_details=SimpleNamespace(category="cyber")), "ERROR_MODEL_REFUSED", "cyber"),
        (text_reply("", stop_reason="refusal"), "ERROR_MODEL_REFUSED", "declined"),  # SDKs without stop_details still work
        (text_reply('{"tool": "stat', stop_reason="max_tokens"), "ERROR_MODEL_TRUNCATED", str(ANTHROPIC_MAX_TOKENS)),
        (text_reply("   \n"), "ERROR_MODEL_EMPTY_RESPONSE", "no text"),
        (SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking="only thinking")], stop_reason="end_turn"), "ERROR_MODEL_EMPTY_RESPONSE", "no text"),
    ],
    ids=["refusal-with-category", "refusal", "truncated", "blank", "thinking-only"],
)
def test_a_refusal_a_cut_off_or_empty_reply_is_an_error_not_an_action(live: type[FakeClient], reply: Any, status: str, mentions: str) -> None:
    live.behaviour = staticmethod(lambda **kwargs: reply)
    with pytest.raises(ModelProviderError) as info:
        provider_chat("anthropic", "m")(MESSAGES)
    assert info.value.status == status and mentions in info.value.detail


# --- SDK failures, on the SDK's real exception classes --------------------------------------------------------------


@pytest.mark.parametrize(
    ("make", "status"),
    [
        (lambda: status_error(anthropic.AuthenticationError, 401, "invalid x-api-key"), "ERROR_MODEL_AUTH_REJECTED"),
        (lambda: status_error(anthropic.PermissionDeniedError, 403), "ERROR_MODEL_AUTH_REJECTED"),
        (lambda: status_error(anthropic.NotFoundError, 404, "model: nope"), "ERROR_MODEL_NOT_FOUND"),
        (lambda: status_error(anthropic.RateLimitError, 429), "ERROR_MODEL_RATE_LIMITED"),
        (lambda: status_error(anthropic.BadRequestError, 400, "max_tokens too large"), "ERROR_MODEL_REQUEST_REJECTED"),
        (lambda: status_error(anthropic.InternalServerError, 500), "ERROR_MODEL_UNAVAILABLE"),
        (lambda: status_error(anthropic.APIStatusError, 529, "overloaded"), "ERROR_MODEL_UNAVAILABLE"),
        (lambda: status_error(anthropic.ConflictError, 409), "ERROR_MODEL_HTTP_ERROR"),
        (lambda: anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")), "ERROR_MODEL_UNAVAILABLE"),
        (lambda: anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")), "ERROR_MODEL_UNAVAILABLE"),
    ],
    ids=["401", "403", "404", "429", "400", "500", "529", "409", "timeout", "connection"],
)
def test_sdk_errors_become_specific_provider_statuses(live: type[FakeClient], make: Any, status: str) -> None:
    def fail(**kwargs: Any) -> Any:
        raise make()

    live.behaviour = staticmethod(fail)
    with pytest.raises(ModelProviderError) as info:
        provider_chat("anthropic", "m")(MESSAGES)
    assert info.value.status == status


def test_error_text_is_bounded_and_never_carries_the_key(live: type[FakeClient]) -> None:
    echoing = f"invalid x-api-key {KEY} " + "padding " * 200

    def fail(**kwargs: Any) -> Any:
        raise status_error(anthropic.AuthenticationError, 401, echoing)

    live.behaviour = staticmethod(fail)
    with pytest.raises(ModelProviderError) as info:
        provider_chat("anthropic", "m")(MESSAGES)

    assert KEY not in info.value.detail and "[redacted]" in info.value.detail
    assert len(info.value.detail) <= PROVIDER_DETAIL_CHARS + 120  # the bounded message plus the fixed explanation


def test_a_programming_error_is_not_dressed_up_as_a_provider_failure(live: type[FakeClient]) -> None:
    def fail(**kwargs: Any) -> Any:
        raise KeyError("a bug in this process, not the API")

    live.behaviour = staticmethod(fail)
    with pytest.raises(KeyError):
        provider_chat("anthropic", "m")(MESSAGES)


def test_a_provider_failure_mid_run_ends_the_run_cleanly_and_keeps_the_audit_trail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], live: type[FakeClient]
) -> None:
    cfg, repo = granted_repo(tmp_path)
    (repo / ".env").write_text("TOKEN=dummy-not-a-secret\n", encoding="utf-8")
    replies = iter([text_reply(json.dumps({"tool": "read", "args": {"path": ".env"}}))])  # needs approval; --yes gives it

    def behaviour(**kwargs: Any) -> Any:
        try:
            return next(replies)
        except StopIteration:
            raise status_error(anthropic.RateLimitError, 429, "slow down") from None

    live.behaviour = staticmethod(behaviour)
    argv = ["code", "agent", "read it", "--repo", str(repo), "--provider", "anthropic", "--model", "m", "--yes"]

    assert main(argv, cfg) == 2

    captured = capsys.readouterr()
    assert json.loads(captured.err)["status"] == "ERROR_MODEL_RATE_LIMITED" and "Traceback" not in captured.err
    assert [e["event"] for e in list_tool_audit_events(cfg)["events"]] == ["TOOL_CALL_REQUESTED", "TOOL_CALL_APPROVED", "TOOL_CALL_EXECUTED"]
    assert len(live.calls) == 2  # the answered step, then the failing one


def test_the_agent_module_has_no_other_route_to_the_live_api() -> None:
    # `provider_chat` is the single place the agent reaches Anthropic; it must not be bypassable by importing the SDK elsewhere.
    source = Path(coding_agent.__file__).read_text(encoding="utf-8")
    assert source.count("import anthropic") == 1 and "from anthropic import" not in source
