"""Bounded entry-controller checks; does not certify UI, migration, or dispatch."""
from dataclasses import replace

import pytest

from backend.app.execution_entry import EntryError, EntryRequest, EntryStore


@pytest.fixture
def store(tmp_path):
    instance = EntryStore(str(tmp_path / "entry.sqlite3"))
    instance.initialize()
    instance.seed(mode="GOAL", object_id="vector-001", revision=1, version=1, state="VECTOR_READY")
    instance.seed(mode="PLAN", object_id="plan-001", revision=1, version=1, state="PLAN_APPROVED")
    return instance


def request(mode="GOAL", action="Proceed", **changes):
    values = dict(mode=mode, action=action, object_id="vector-001" if mode == "GOAL" else "plan-001",
                  revision=1, expected_version=1, actor="operator-1", policy_version="v1",
                  source_commit="0123456789abcdef", idempotency_key=f"{mode}-{action}")
    values.update(changes)
    return EntryRequest(**values)


@pytest.mark.parametrize("mode,action,state", [
    ("GOAL", "Proceed", "ENTRY_CLAIMED"),
    ("GOAL", "Adjust", "VECTOR_CANDIDATE"),
    ("PLAN", "Execute", "ENTRY_CLAIMED"),
    ("PLAN", "Revise", "PLAN_CANDIDATE"),
])
def test_four_actions(store, mode, action, state):
    receipt = store.decide(request(mode, action), allowed=True, gates={"dependency": "PASS"})
    assert receipt["result_state"] == state
    assert receipt["dispatch_authorized"] is False
    assert receipt["result_revision"] == (1 if action in {"Proceed", "Execute"} else 2)


@pytest.mark.parametrize("mode,action", [("GOAL", "Proceed"), ("PLAN", "Execute")])
def test_idempotent_replay(store, mode, action):
    req = request(mode, action)
    assert store.decide(req, allowed=True, gates={"dependency": "PASS"}) == store.decide(
        req, allowed=True, gates={"dependency": "PASS"}
    )


@pytest.mark.parametrize("change,error", [
    ({"revision": 2}, "ERROR_ENTRY_STALE_REVISION"),
    ({"expected_version": 2}, "ERROR_ENTRY_STALE_REVISION"),
    ({"object_id": ""}, "ERROR_ENTRY_MISSING_IDENTITY"),
    ({"actor": ""}, "ERROR_ENTRY_MISSING_IDENTITY"),
    ({"idempotency_key": ""}, "ERROR_ENTRY_MISSING_IDENTITY"),
    ({"action": "Execute"}, "ERROR_ENTRY_ACTION_MODE_MISMATCH"),
])
def test_invalid_input_fails_closed(store, change, error):
    with pytest.raises(EntryError, match=error):
        store.decide(request(**change), allowed=True, gates={"dependency": "PASS"})


def test_authorization_denied(store):
    with pytest.raises(EntryError, match="ERROR_ENTRY_UNAUTHORIZED"):
        store.decide(request(), allowed=False, gates={"dependency": "PASS"})


@pytest.mark.parametrize("gates", [{}, {"dependency": "FAIL"}, {"dependency": "OPEN"},
                                    {"dependency": "UNRESOLVED"}, {"dependency": "PASS", "policy": "BLOCKED"}])
def test_unsatisfied_gates(store, gates):
    with pytest.raises(EntryError, match="ERROR_ENTRY_GATE_UNSATISFIED"):
        store.decide(request(), allowed=True, gates=gates)


def test_idempotency_key_payload_conflict(store):
    req = request()
    store.decide(req, allowed=True, gates={"dependency": "PASS"})
    with pytest.raises(EntryError, match="ERROR_ENTRY_IDEMPOTENCY_CONFLICT"):
        store.decide(replace(req, actor="different"), allowed=True, gates={"dependency": "PASS"})


def test_competing_claims(store):
    store.decide(request(idempotency_key="one"), allowed=True, gates={"dependency": "PASS"})
    with pytest.raises(EntryError, match="ERROR_ENTRY_ALREADY_ACTIVE"):
        store.decide(request(idempotency_key="two"), allowed=True, gates={"dependency": "PASS"})


def test_stale_after_revision(store):
    store.decide(request(action="Adjust"), allowed=True, gates={})
    with pytest.raises(EntryError, match="ERROR_ENTRY_STALE_REVISION"):
        store.decide(request(idempotency_key="different"), allowed=True, gates={"dependency": "PASS"})


def test_restart_retains_decision(tmp_path):
    path = str(tmp_path / "persist.sqlite3")
    one = EntryStore(path)
    one.initialize()
    one.seed(mode="GOAL", object_id="vector-001", revision=1, version=1, state="VECTOR_READY")
    req = request()
    expected = one.decide(req, allowed=True, gates={"dependency": "PASS"})
    two = EntryStore(path)
    two.initialize()
    assert two.decide(req, allowed=True, gates={"dependency": "PASS"}) == expected
