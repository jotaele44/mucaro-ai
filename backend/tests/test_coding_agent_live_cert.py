"""The coding-agent live certifier (S6): PASS needs executed evidence, FAIL and BLOCKED are told apart, no secret leaks."""

from __future__ import annotations

import hashlib
import http.server
import json
import re
import threading
from pathlib import Path
from typing import Any

import pytest

from backend.app.model_router import ModelProviderError
from scripts import certify_coding_agent_live as cert

MARKER = re.compile(r"MUCARO-LIVE-CERT-[0-9a-f]{16}")
HEAD = re.compile(r'"head_sha": "([0-9a-f]{40})"')
KEY = "sk-ant-test-0123456789-NOT-A-REAL-KEY"


def honest_reply(turn: int, user_text: str) -> str:
    """What a well-behaved model says on each turn: use the tools, then report what the tool results said."""
    if turn == 1:
        return json.dumps({"tool": "status"})
    if turn == 2:
        return json.dumps({"tool": "read", "args": {"path": "MARKER.txt"}})
    marker, head = MARKER.search(user_text), HEAD.search(user_text)
    assert marker and head, "the tool results must have reached the model"
    return json.dumps({"final": f"HEAD {head.group(1)[:7]}, marker {marker.group(0)}"})


def model(*replies: Any) -> Any:
    """A chat factory for a scripted model: strings are replies, callables compute one from (turn, user_text)."""

    def factory(provider: str, model_name: str) -> Any:
        turn = {"n": 0}

        def chat(messages: list[dict[str, str]]) -> str:
            turn["n"] += 1
            reply = replies[min(turn["n"], len(replies)) - 1] if replies else honest_reply
            user_text = "\n".join(m["content"] for m in messages if m["role"] == "user")
            return reply(turn["n"], user_text) if callable(reply) else reply

        return chat

    return factory


def raising(status: str) -> Any:
    def factory(provider: str, model_name: str) -> Any:
        raise ModelProviderError(status, f"{status} detail")

    return factory


def verify_hash(receipt: dict[str, Any]) -> bool:
    body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest() == receipt["receipt_sha256"]


# --- PASS needs the evidence, FAIL is told apart from BLOCKED ---------------------------------------------------------


def test_an_honest_model_passes_with_every_invariant_true(tmp_path: Path) -> None:
    receipt = cert.certify("anthropic", "model-x", chat_factory=model(honest_reply), workdir=tmp_path)

    assert receipt["status"] == cert.STATUS_OK and all(receipt["invariants"].values())
    assert [(s["tool"], s["outcome_status"]) for s in receipt["steps"]] == [("status", "CODING_REPOSITORY_STATUS_OK"), ("read", "CODING_FILE_READ_OK")]
    assert receipt["provider"] == "anthropic" and receipt["model"] == "model-x" and receipt["invalid_action_steps"] == 0
    assert MARKER.search(receipt["final_excerpt"]) and receipt["source"]["tree"] in {"CLEAN", "DIRTY"} and len(receipt["source"]["head_sha"]) == 40
    assert verify_hash(receipt) and receipt["transcript_sha256"] and "limitations" in receipt


def test_the_marker_is_random_per_run_so_it_cannot_be_memorised(tmp_path: Path) -> None:
    first = cert.certify("local", "m", chat_factory=model(honest_reply), workdir=tmp_path / "a")
    second = cert.certify("local", "m", chat_factory=model(honest_reply), workdir=tmp_path / "b")
    assert MARKER.search(first["final_excerpt"]).group(0) != MARKER.search(second["final_excerpt"]).group(0)


def test_a_model_that_guesses_instead_of_using_the_tools_fails(tmp_path: Path) -> None:
    guess = json.dumps({"final": "HEAD abc1234, marker MUCARO-LIVE-CERT-0000000000000000"})

    receipt = cert.certify("anthropic", "m", chat_factory=model(guess), workdir=tmp_path)

    assert receipt["status"] == cert.STATUS_FAILED
    invariants = receipt["invariants"]
    assert invariants["agent_completed"] is True  # it finished, but without evidence
    assert invariants["status_tool_succeeded"] is False and invariants["final_contains_marker_from_tool_result"] is False


def test_a_model_that_reads_but_never_finishes_fails(tmp_path: Path) -> None:
    receipt = cert.certify("anthropic", "m", chat_factory=model(json.dumps({"tool": "status"})), workdir=tmp_path, max_steps=3)

    assert receipt["status"] == cert.STATUS_FAILED and receipt["invariants"]["agent_completed"] is False and receipt["step_count"] == 3


def test_evidence_obtained_only_through_a_sub_agent_is_not_direct_tool_use(tmp_path: Path) -> None:
    sha = re.compile(r"\b([0-9a-f]{40})\b")

    def delegating(turn: int, user_text: str) -> str:
        # Replies are consumed in order by the parent and then its sub-agent, which share one scripted model.
        if turn == 1:
            return json.dumps({"tool": "delegate", "args": {"goal": "report MARKER.txt and the HEAD commit"}})
        if turn == 2:
            return json.dumps({"tool": "status"})
        if turn == 3:
            return json.dumps({"tool": "read", "args": {"path": "MARKER.txt"}})
        if turn == 4:  # the sub-agent has now seen both tool results and reports them
            return json.dumps({"final": f"HEAD {HEAD.search(user_text).group(1)} marker {MARKER.search(user_text).group(0)}"})
        return json.dumps({"final": f"HEAD {sha.search(user_text).group(1)[:7]}, marker {MARKER.search(user_text).group(0)}"})

    receipt = cert.certify("anthropic", "m", chat_factory=model(delegating), workdir=tmp_path, max_steps=5)

    invariants = receipt["invariants"]
    assert invariants["final_contains_marker_from_tool_result"] and invariants["final_contains_head_prefix"]  # the content is right...
    assert invariants["status_tool_succeeded"] is False and invariants["read_tool_succeeded"] is False  # ...but not gathered directly
    assert receipt["status"] == cert.STATUS_FAILED and [s["tool"] for s in receipt["steps"]] == ["delegate"]


def test_a_model_replying_in_prose_is_counted_but_can_still_pass_when_it_recovers(tmp_path: Path) -> None:
    def recovering(turn: int, user_text: str) -> str:
        return "I will look at the repository now." if turn == 1 else honest_reply(turn - 1, user_text)

    receipt = cert.certify("anthropic", "m", chat_factory=model(recovering), workdir=tmp_path, max_steps=5)

    assert receipt["status"] == cert.STATUS_OK and receipt["invalid_action_steps"] == 1  # the protocol slip is evidence, not hidden


def test_mutations_are_refused_and_do_not_affect_the_verdict(tmp_path: Path) -> None:
    def tries_to_write(turn: int, user_text: str) -> str:
        return json.dumps({"tool": "apply_patch", "args": {"patch": "diff --git a/MARKER.txt b/MARKER.txt\n"}}) if turn == 1 else honest_reply(turn - 1, user_text)

    receipt = cert.certify("anthropic", "m", chat_factory=model(tries_to_write), workdir=tmp_path, max_steps=5)

    assert receipt["status"] == cert.STATUS_OK
    assert receipt["steps"][0] == {"step": 1, "tool": "apply_patch", "outcome_status": "ERROR_AGENT_APPROVAL_DENIED"}
    assert receipt["invariants"]["no_mutating_step_executed"] and receipt["invariants"]["repository_clean_after"]


def test_a_provider_that_answers_badly_is_a_failure_not_a_blocker(tmp_path: Path) -> None:
    for status in ("ERROR_MODEL_REFUSED", "ERROR_MODEL_TRUNCATED", "ERROR_MODEL_EMPTY_RESPONSE", "ERROR_MODEL_REQUEST_REJECTED", "ERROR_LOCAL_MODEL_RESPONSE_INVALID"):
        receipt = cert.certify("anthropic", "m", chat_factory=raising(status), workdir=tmp_path / status)
        assert receipt["status"] == cert.STATUS_FAILED and receipt["failure"]["status"] == status, status


@pytest.mark.parametrize("status", sorted(cert.BLOCKING_PROVIDER_STATUSES))
def test_a_missing_precondition_is_blocked_and_claims_nothing(tmp_path: Path, status: str) -> None:
    receipt = cert.certify("anthropic", "m", chat_factory=raising(status), workdir=tmp_path)

    assert receipt["status"] == cert.STATUS_BLOCKED and receipt["blocked_on"] == {"status": status, "detail": f"{status} detail"}
    assert receipt["evidence"].startswith("none") and "invariants" not in receipt and "steps" not in receipt
    assert verify_hash(receipt)


def test_a_failure_part_way_through_a_run_is_classified_the_same_way(tmp_path: Path) -> None:
    def rate_limited_on_the_second_turn(provider: str, model_name: str) -> Any:
        turn = {"n": 0}

        def chat(messages: list[dict[str, str]]) -> str:
            turn["n"] += 1
            if turn["n"] == 2:
                raise ModelProviderError("ERROR_MODEL_RATE_LIMITED", "slow down")
            return json.dumps({"tool": "status"})

        return chat

    receipt = cert.certify("anthropic", "m", chat_factory=rate_limited_on_the_second_turn, workdir=tmp_path)
    assert receipt["status"] == cert.STATUS_BLOCKED and receipt["blocked_on"]["status"] == "ERROR_MODEL_RATE_LIMITED"


# --- the real provider layer, with nothing faked but the far end -----------------------------------------------------


def test_missing_live_mode_and_an_unreachable_local_server_are_blocked_for_real(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MUCARO_AI_LIVE_MODE", raising=False)
    receipt = cert.certify("anthropic", "m", workdir=tmp_path / "live")
    assert receipt["status"] == cert.STATUS_BLOCKED and receipt["blocked_on"]["status"] == "ERROR_LIVE_MODE_DISABLED"

    monkeypatch.setenv("MUCARO_AI_LOCAL_MODEL_URL", "http://127.0.0.1:9/v1")  # the discard port: nothing listens
    receipt = cert.certify("local", "m", workdir=tmp_path / "local")
    assert receipt["status"] == cert.STATUS_BLOCKED and receipt["blocked_on"]["status"] == "ERROR_LOCAL_MODEL_UNAVAILABLE"

    monkeypatch.setenv("MUCARO_AI_LOCAL_MODEL_URL", "http://example.com/v1")
    receipt = cert.certify("local", "m", workdir=tmp_path / "remote")
    assert receipt["status"] == cert.STATUS_BLOCKED and receipt["blocked_on"]["status"] == "ERROR_LOCAL_MODEL_URL_NOT_LOOPBACK"


class FakeModelServer:
    """A loopback OpenAI-compatible server whose model behaves honestly, deciding its turn from the conversation it is sent."""

    def __init__(self) -> None:
        owner = self
        self.requests: list[dict[str, Any]] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                owner.requests.append(request)
                messages = request["messages"]
                turn = sum(1 for m in messages if m["role"] == "assistant") + 1
                reply = honest_reply(turn, "\n".join(m["content"] for m in messages if m["role"] == "user"))
                body = json.dumps({"choices": [{"message": {"content": reply}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_: object) -> None:
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1"


def test_the_whole_stack_passes_against_a_real_local_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = FakeModelServer()
    try:
        monkeypatch.setenv("MUCARO_AI_LOCAL_MODEL_URL", server.url)
        receipt = cert.certify("local", "honest-model", workdir=tmp_path)
    finally:
        server.server.shutdown()

    assert receipt["status"] == cert.STATUS_OK and all(receipt["invariants"].values())
    assert {request["model"] for request in server.requests} == {"honest-model"} and len(server.requests) == 3
    first = server.requests[0]["messages"]
    assert first[0]["role"] == "system" and "<tool_result>" in first[0]["content"] and first[1]["content"] == cert.GOAL


# --- the command line ----------------------------------------------------------------------------------------------


def test_main_writes_the_receipt_and_never_leaks_the_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.setattr(cert, "provider_chat", model(honest_reply))
    output = tmp_path / "out" / "receipt.json"

    assert cert.main(["--provider", "anthropic", "--model", "model-x", "--output", str(output)]) == 0

    captured = capsys.readouterr()
    assert captured.out.splitlines()[0] == cert.STATUS_OK
    written = output.read_text(encoding="utf-8")
    assert json.loads(written)["status"] == cert.STATUS_OK and verify_hash(json.loads(written))
    assert KEY not in written and KEY not in captured.out and KEY not in captured.err


def test_exit_codes_distinguish_pass_fail_and_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    args = ["--provider", "anthropic", "--model", "m"]
    monkeypatch.setattr(cert, "provider_chat", model(honest_reply))
    assert cert.main(args) == 0
    monkeypatch.setattr(cert, "provider_chat", model(json.dumps({"final": "no evidence"})))
    assert cert.main(args) == 2
    monkeypatch.setattr(cert, "provider_chat", raising("ERROR_MODEL_API_KEY_MISSING"))
    assert cert.main(args) == 3
    capsys.readouterr()


def test_usage_errors_are_rejected_before_anything_runs(capsys: pytest.CaptureFixture[str]) -> None:
    for argv in (["--provider", "anthropic"], ["--model", "m"], ["--provider", "mock", "--model", "m"], ["--provider", "local", "--model", "m", "--max-steps", "0"]):
        with pytest.raises(SystemExit) as info:
            cert.main(argv)
        assert info.value.code == 2, argv
    capsys.readouterr()
