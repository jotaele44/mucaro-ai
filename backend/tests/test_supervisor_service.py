"""Process restart and allowlist gates for the standalone probe supervisor."""
import json
import os
import subprocess
import sys
from pathlib import Path

from app.supervisor_queue import claim, enqueue, get_task
from app.supervisor_service import PROBE_KIND

ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40


def launch(db):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "backend"))
    return subprocess.run(
        [sys.executable, "-m", "app.supervisor_service", "--db", str(db), "--once"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=12,
    )


def submit(db, name, kind=PROBE_KIND, mutates=False):
    return enqueue(
        db, task_id=name, idempotency_key=name,
        payload={"kind": kind, "source_sha": SHA, "run_id": name},
        mutates_state=mutates, now=1,
    )


def test_separate_process_restarts_and_executes_readonly(tmp_path):
    db = tmp_path / "probe.db"
    submit(db, "probe")
    first = launch(db)
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["status"] == "COMMITTED"
    assert get_task(db, "probe")["state"] == "COMPLETED"
    second = launch(db)
    assert second.returncode == 0
    assert json.loads(second.stdout)["status"] == "IDLE"
    assert get_task(db, "probe")["generation"] == 1


def test_expired_crash_claim_recovers_only_readonly(tmp_path):
    db = tmp_path / "probe.db"
    submit(db, "probe")
    old = claim(db, worker_id="dead-worker", lease_seconds=2, now=1)
    result = launch(db)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["recovery"]["requeued_read_only"] == 1
    assert get_task(db, "probe")["state"] == "COMPLETED"
    assert get_task(db, "probe")["generation"] == old["generation"] + 1


def test_mutation_is_not_claimed_or_executed(tmp_path):
    db = tmp_path / "probe.db"
    submit(db, "mutation", mutates=True)
    result = launch(db)
    assert result.returncode == 0
    assert json.loads(result.stdout)["status"] == "IDLE"
    assert get_task(db, "mutation")["state"] == "QUEUED"


def test_unknown_kind_is_not_dispatched(tmp_path):
    db = tmp_path / "probe.db"
    submit(db, "unknown", kind="coding.exec")
    result = launch(db)
    assert result.returncode == 0
    assert json.loads(result.stdout)["status"] == "IDLE"
    assert get_task(db, "unknown")["state"] == "QUEUED"


def test_mutation_crash_requires_reconciliation(tmp_path):
    db = tmp_path / "probe.db"
    submit(db, "mutation", mutates=True)
    claim(db, worker_id="dead-worker", lease_seconds=2, now=1)
    result = launch(db)
    assert result.returncode == 0
    assert json.loads(result.stdout)["recovery"]["blocked_mutations"] == 1
    assert get_task(db, "mutation")["state"] == "NEEDS_RECONCILIATION"


def test_killed_worker_recovers_from_persisted_lease(tmp_path):
    """A real worker process dies after claiming, and a new process resumes."""
    db = tmp_path / "probe.db"
    submit(db, "probe")
    env = dict(os.environ, PYTHONPATH=str(ROOT / "backend"))
    child = subprocess.Popen(
        [sys.executable, "-c",
         "import sys,time; from app.supervisor_queue import claim; "
         "claim(sys.argv[1],worker_id='killed-worker',lease_seconds=0.2); "
         "print('LEASED',flush=True); time.sleep(30)", str(db)],
        cwd=ROOT, env=env, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout.readline().strip() == "LEASED"
        assert get_task(db, "probe")["state"] == "LEASED"
        child.kill()
        child.wait(timeout=5)
        assert child.returncode != 0
        import time
        time.sleep(0.3)
        result = launch(db)
        assert result.returncode == 0, result.stderr
        assert get_task(db, "probe")["state"] == "COMPLETED"
        assert get_task(db, "probe")["generation"] == 2
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
