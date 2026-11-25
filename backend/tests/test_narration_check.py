"""MOB-F15: an answer that claims an action the call records do not show as executed is flagged."""
from __future__ import annotations

import pytest

from backend.app.narration_check import NARRATION_WARNING, narration_conflicts
from backend.app.tools import deny_tool_call
from backend.tests.test_agent_loop import _Scripted, _call, _events, _resume, _submit, _text, cfg, server  # noqa: F401


def call(tool: str, status: str, call_id: str = "tc1") -> dict:
    return {"tool_call_id": call_id, "tool_name": tool, "status": status}


@pytest.mark.parametrize(
    ("answer", "claim"),
    [
        ("I committed the fix on a new branch.", "COMMIT"),
        ("Your changes have been committed.", "COMMIT"),
        ("I've pushed the branch to origin.", "PUSH"),
        ("I opened a draft pull request for you.", "PULL_REQUEST"),
        ("I created a new branch called fix-1.", "BRANCH"),
        ("I applied the patch to app.py.", "PATCH"),
        ("I ran the tests and all 12 passed.", "EXECUTION"),
        ("I fetched the page and it says the release is on Tuesday.", "WEB"),
        ("We have successfully cloned the repository.", "CLONE"),
    ],
)
def test_a_claimed_action_without_a_record_is_a_conflict(answer: str, claim: str) -> None:
    [conflict] = narration_conflicts(answer, [])
    assert conflict["claim"] == claim
    assert conflict["reason"] == "NO_RECORD"
    assert conflict["phrase"] and conflict["phrase"].lower() in answer.lower()


@pytest.mark.parametrize("status", ["DENIED", "FAILED", "CANCELLED", "INTERRUPTED", "PENDING_APPROVAL"])
def test_a_claimed_action_whose_record_did_not_execute_is_a_conflict(status: str) -> None:
    [conflict] = narration_conflicts("I pushed your branch.", [call("coding.push", status)])
    assert conflict["reason"] == f"RECORD_{status}"
    assert conflict["tool_call_ids"] == ["tc1"]


@pytest.mark.parametrize(
    "answer",
    [
        "I will push the branch once you approve it.",
        "I did not commit anything; the change is waiting for your approval.",
        "I couldn't fetch the page because the request was denied.",
        "Should I open a pull request?",
        "I'm committed to keeping your data local.",
        "The library holds one source.",
    ],
)
def test_plans_refusals_and_questions_are_not_claims(answer: str) -> None:
    assert narration_conflicts(answer, []) == []


def test_a_claim_backed_by_an_executed_record_is_not_a_conflict() -> None:
    calls = [call("coding.push", "FAILED", "a"), call("coding.push", "EXECUTED", "b")]
    assert narration_conflicts("I pushed the branch.", calls) == []
    assert narration_conflicts("I ran the tests.", [call("coding.regression_run", "EXECUTED")]) == []


def test_a_run_whose_answer_claims_a_denied_fetch_carries_the_warning(server, cfg):  # noqa: F811
    _Scripted.script = [_call("web.fetch", {"url": "https://example.com/a"}), _text("I fetched the page: it says the launch moved to May.")]
    run = _submit(cfg, "what does example.com/a say?")
    request = _events(cfg, run["run_id"], "approval_request")[0]
    deny_tool_call(request["tool_call_id"], cfg=cfg, denied_by="operator", expected_arguments_sha256=request["arguments_sha256"])
    done = _resume(cfg, run["run_id"])
    assert done["outcome"] == "ANSWERED"
    [warning] = [data for data in _events(cfg, run["run_id"], "warning") if data["status"] == NARRATION_WARNING]
    assert warning["category"] == "TOOL_FAILURE"
    assert warning["conflicts"][0]["claim"] == "WEB"
    assert warning["conflicts"][0]["reason"] == "RECORD_DENIED"
    assert warning["conflicts"][0]["tool_call_ids"] == [request["tool_call_id"]]


def test_a_run_whose_answer_matches_its_records_carries_no_warning(server, cfg):  # noqa: F811
    _Scripted.script = [_call("web.fetch", {"url": "https://example.com/a"}), _text("I could not fetch the page because you denied it.")]
    run = _submit(cfg, "what does example.com/a say?")
    request = _events(cfg, run["run_id"], "approval_request")[0]
    deny_tool_call(request["tool_call_id"], cfg=cfg, denied_by="operator", expected_arguments_sha256=request["arguments_sha256"])
    _resume(cfg, run["run_id"])
    assert not [data for data in _events(cfg, run["run_id"], "warning") if data["status"] == NARRATION_WARNING]
