"""Bounded durable-agent scheduler regression tests."""
from pathlib import Path

import pytest

from backend.app.durable_agent_loop import AgentRunError, AgentRunStore
from backend.app.governed_learning_loop import digest


def new_store(tmp_path: Path):
    path = tmp_path / "agent.sqlite3"
    store = AgentRunStore(path)
    store.create("r1", "test goal", digest("in"), digest("policy"), digest("executor"))
    return store, path


def test_idempotent_create_and_identity_conflict(tmp_path):
    s, path = new_store(tmp_path)
    s.create("r1", "test goal", digest("in"), digest("policy"), digest("executor"))
    with pytest.raises(AgentRunError, match="RUN_IDENTITY_CONFLICT"):
        s.create("r1", "changed", digest("in"), digest("policy"), digest("executor"))
    s.close()


def test_restart_preserves_receipt_and_exact_once_completed_phase(tmp_path):
    s, path = new_store(tmp_path)
    calls = []
    def executor(run_id, phase):
        calls.append((run_id, phase))
        return {"status": "PASS", "phase": phase}
    assert s.advance("r1", policy_check=lambda _: True, execute=executor)["state"] == "SUCCEEDED"
    before = s.receipt("r1")
    s.close()
    s = AgentRunStore(path)
    assert s.receipt("r1") == before
    assert s.advance("r1", policy_check=lambda _: True, execute=executor)["phase"] == "UNDERSTAND"
    assert calls == [("r1", "PERCEIVE"), ("r1", "UNDERSTAND")]
    s.close()


def test_policy_denial_is_terminal_and_does_not_execute(tmp_path):
    s, _ = new_store(tmp_path)
    calls = []
    def exec_fn(*args):
        calls.append(args)
        return {"status": "PASS"}
    result = s.advance("r1", policy_check=lambda _: False, execute=exec_fn)
    assert result["state"] == "BLOCKED"
    assert calls == []
    assert s.advance("r1", policy_check=lambda _: True, execute=exec_fn)["status"] == "BLOCKED"
    assert calls == []
    s.close()


def test_nonpass_executor_fails_closed(tmp_path):
    s, _ = new_store(tmp_path)
    assert s.advance("r1", policy_check=lambda _: True,
                     execute=lambda *_: {"status": "OPEN"})["state"] == "FAILED"
    assert s.advance("r1", policy_check=lambda _: True,
                     execute=lambda *_: {"status": "PASS"})["status"] == "FAILED"
    s.close()


def test_exception_fails_closed_without_leaking_secret(tmp_path):
    s, _ = new_store(tmp_path)
    def failing(*_):
        raise RuntimeError("password=should-not-log")
    assert s.advance("r1", policy_check=lambda _: True, execute=failing)["state"] == "FAILED"
    assert "should-not-log" not in str(s.receipt("r1"))
    s.close()


def test_promotion_and_monitor_are_not_executable_v1(tmp_path):
    s, _ = new_store(tmp_path)
    executed = []
    def exec_fn(_, phase):
        executed.append(phase)
        return {"status": "PASS", "phase": phase}
    for phase in ("PERCEIVE", "UNDERSTAND", "PLAN", "EXECUTE", "OBSERVE",
                  "EVALUATE", "LEARN", "OPTIMIZE", "VERIFY"):
        assert s.advance("r1", policy_check=lambda _: True, execute=exec_fn)["phase"] == phase
    assert s.advance("r1", policy_check=lambda _: True, execute=exec_fn,
                     allow_promotion=True)["state"] == "BLOCKED"
    assert executed[-1] == "VERIFY"
    assert "PROMOTE" not in executed
    s.close()


def test_missing_run_denied(tmp_path):
    s = AgentRunStore(tmp_path / "run.sqlite3")
    with pytest.raises(AgentRunError, match="RUN_NOT_FOUND"):
        s.advance("unknown", policy_check=lambda _: True,
                  execute=lambda *_: {"status": "PASS"})
    s.close()


def test_event_hash_chain(tmp_path):
    s, _ = new_store(tmp_path)
    for _ in range(3):
        s.advance("r1", policy_check=lambda _: True,
                  execute=lambda *_: {"status": "PASS"})
    receipt = s.receipt("r1")
    assert receipt["events"][0]["event"]["prev_hash"] is None
    for old, new in zip(receipt["events"], receipt["events"][1:]):
        assert new["event"]["prev_hash"] == old["event_hash"]
    s.close()
