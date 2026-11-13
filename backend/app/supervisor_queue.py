"""Durable, fail-closed task dispatch primitives for the future agent supervisor.

No thread or worker is started here. A caller must explicitly verify policy
before dispatch; expired potentially-mutating leases are NEVER replayed.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

STATES = frozenset({"QUEUED", "LEASED", "COMPLETED", "FAILED", "NEEDS_RECONCILIATION"})
SCHEMA = """
CREATE TABLE IF NOT EXISTS supervisor_tasks (
    task_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    mutates_state INTEGER NOT NULL CHECK(mutates_state IN (0,1)),
    state TEXT NOT NULL,
    generation INTEGER NOT NULL DEFAULT 0,
    owner TEXT,
    lease_until REAL,
    receipt_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS supervisor_dispatch_idx ON supervisor_tasks(state, created_at);
"""


class SupervisorQueueError(RuntimeError):
    pass


def _connect(db_path: str | Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(db_path), timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=10000")
    db.executescript(SCHEMA)
    return db


def _item(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "task_id": row["task_id"],
        "idempotency_key": row["idempotency_key"],
        "payload": json.loads(row["payload_json"]),
        "mutates_state": bool(row["mutates_state"]),
        "state": row["state"],
        "generation": row["generation"],
        "owner": row["owner"],
        "lease_until": row["lease_until"],
        "receipt": json.loads(row["receipt_json"]) if row["receipt_json"] else None,
    }


def enqueue(
    db_path: str | Path, *,
    task_id: str, idempotency_key: str, payload: dict[str, Any],
    mutates_state: bool, now: float | None = None,
) -> dict[str, Any]:
    """Identical retries return the original task; conflicting retries fail closed."""
    if not task_id or not idempotency_key or not isinstance(payload, dict) or not isinstance(mutates_state, bool):
        raise SupervisorQueueError("invalid task identity or payload")
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    timestamp = time.time() if now is None else now
    with _connect(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT * FROM supervisor_tasks WHERE task_id=? OR idempotency_key=?",
            (task_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            if (existing["task_id"], existing["idempotency_key"], existing["payload_json"], bool(existing["mutates_state"])) != (
                task_id, idempotency_key, raw, mutates_state
            ):
                raise SupervisorQueueError("idempotency conflict")
            return _item(existing)
        db.execute(
            """INSERT INTO supervisor_tasks
            (task_id,idempotency_key,payload_json,mutates_state,state,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?)""",
            (task_id, idempotency_key, raw, int(mutates_state), "QUEUED", timestamp, timestamp),
        )
        return _item(db.execute("SELECT * FROM supervisor_tasks WHERE task_id=?", (task_id,)).fetchone())


def claim(
    db_path: str | Path, *, worker_id: str, lease_seconds: float = 30,
    now: float | None = None,
) -> dict[str, Any] | None:
    """Atomically claim one queued task. This DOES NOT authorize execution."""
    if not worker_id or not (0 < lease_seconds <= 3600):
        raise SupervisorQueueError("invalid worker or lease")
    timestamp = time.time() if now is None else now
    with _connect(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM supervisor_tasks WHERE state='QUEUED' ORDER BY created_at,task_id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        db.execute(
            """UPDATE supervisor_tasks SET state='LEASED',owner=?,lease_until=?,
            generation=generation+1,updated_at=? WHERE task_id=? AND state='QUEUED'""",
            (worker_id, timestamp + lease_seconds, timestamp, row["task_id"]),
        )
        return _item(db.execute("SELECT * FROM supervisor_tasks WHERE task_id=?", (row["task_id"],)).fetchone())


def heartbeat(
    db_path: str | Path, *, task_id: str, worker_id: str, generation: int,
    lease_seconds: float = 30, now: float | None = None,
) -> bool:
    timestamp = time.time() if now is None else now
    if not (0 < lease_seconds <= 3600):
        raise SupervisorQueueError("invalid lease")
    with _connect(db_path) as db:
        changed = db.execute(
            """UPDATE supervisor_tasks SET lease_until=?,updated_at=?
            WHERE task_id=? AND state='LEASED' AND owner=? AND generation=?
            AND lease_until>?""",
            (timestamp + lease_seconds, timestamp, task_id, worker_id, generation, timestamp),
        ).rowcount
        return changed == 1


def finish(
    db_path: str | Path, *, task_id: str, worker_id: str, generation: int,
    receipt: dict[str, Any], succeeded: bool, now: float | None = None,
) -> bool:
    """A stale worker cannot commit a result after losing its lease."""
    if not isinstance(receipt, dict) or not receipt:
        raise SupervisorQueueError("nonempty evidence receipt required")
    timestamp = time.time() if now is None else now
    raw = json.dumps(receipt, sort_keys=True, allow_nan=False)
    with _connect(db_path) as db:
        changed = db.execute(
            """UPDATE supervisor_tasks SET state=?,receipt_json=?,updated_at=?,
            owner=NULL,lease_until=NULL WHERE task_id=? AND state='LEASED'
            AND owner=? AND generation=? AND lease_until>?""",
            ("COMPLETED" if succeeded else "FAILED", raw, timestamp, task_id, worker_id, generation, timestamp),
        ).rowcount
        return changed == 1


def reconcile_expired(db_path: str | Path, *, now: float | None = None) -> dict[str, int]:
    """Read-only work may be retried; mutating work awaits external reconciliation."""
    timestamp = time.time() if now is None else now
    with _connect(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        blocked = db.execute(
            """UPDATE supervisor_tasks SET state='NEEDS_RECONCILIATION',
            owner=NULL,lease_until=NULL,updated_at=?
            WHERE state='LEASED' AND lease_until<=? AND mutates_state=1""",
            (timestamp, timestamp),
        ).rowcount
        queued = db.execute(
            """UPDATE supervisor_tasks SET state='QUEUED',
            owner=NULL,lease_until=NULL,updated_at=?
            WHERE state='LEASED' AND lease_until<=? AND mutates_state=0""",
            (timestamp, timestamp),
        ).rowcount
    return {"requeued_read_only": queued, "blocked_mutations": blocked}


def get_task(db_path: str | Path, task_id: str) -> dict[str, Any] | None:
    with _connect(db_path) as db:
        row = db.execute("SELECT * FROM supervisor_tasks WHERE task_id=?", (task_id,)).fetchone()
        return _item(row) if row else None
