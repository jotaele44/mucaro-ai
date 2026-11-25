"""Failure and recovery invariants for the durable queue foundation."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.supervisor_queue import (
    SupervisorQueueError, claim, enqueue, finish, get_task, heartbeat, reconcile_expired,
)


def task(path, name="one", mutates=False):
    return enqueue(path, task_id=name, idempotency_key="key-" + name,
                   payload={"objective": name}, mutates_state=mutates, now=10)


def test_idempotency_rejects_changed_intent(tmp_path):
    db = tmp_path / "tasks.db"
    assert task(db) == task(db)
    with pytest.raises(SupervisorQueueError, match="idempotency conflict"):
        enqueue(db, task_id="one", idempotency_key="key-one",
                payload={"objective": "different"}, mutates_state=False)


def test_atomic_claim_across_workers(tmp_path):
    db = tmp_path / "tasks.db"
    task(db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(lambda worker: claim(db, worker_id=worker, now=10), ("w1", "w2")))
    assert sum(x is not None for x in (a, b)) == 1
    assert get_task(db, "one")["generation"] == 1


def test_expired_mutation_never_replays_automatically(tmp_path):
    db = tmp_path / "tasks.db"
    task(db, mutates=True)
    leased = claim(db, worker_id="w1", lease_seconds=5, now=10)
    assert reconcile_expired(db, now=16) == {"requeued_read_only": 0, "blocked_mutations": 1}
    assert claim(db, worker_id="w2", now=17) is None
    assert not finish(db, task_id="one", worker_id="w1", generation=leased["generation"],
                      receipt={"exit_code": 0}, succeeded=True, now=17)
    assert get_task(db, "one")["state"] == "NEEDS_RECONCILIATION"


def test_read_only_requeue_and_fencing(tmp_path):
    db = tmp_path / "tasks.db"
    task(db)
    old = claim(db, worker_id="w1", lease_seconds=5, now=10)
    assert reconcile_expired(db, now=16)["requeued_read_only"] == 1
    new = claim(db, worker_id="w2", lease_seconds=5, now=17)
    assert new["generation"] == old["generation"] + 1
    assert not heartbeat(db, task_id="one", worker_id="w1", generation=old["generation"], now=18)
    assert not finish(db, task_id="one", worker_id="w1", generation=old["generation"],
                      receipt={"ok": True}, succeeded=True, now=18)
    assert heartbeat(db, task_id="one", worker_id="w2", generation=new["generation"], now=18)
    assert finish(db, task_id="one", worker_id="w2", generation=new["generation"],
                  receipt={"ok": True}, succeeded=True, now=19)
    assert get_task(db, "one")["state"] == "COMPLETED"
