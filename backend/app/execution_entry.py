"""Versioned Goal/Plan entry decisions.

This module is a governed pre-dispatch controller, NOT a replacement for the
work_runs tool authorization runtime. No tool dispatch is performed here.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Literal

Mode = Literal["GOAL", "PLAN"]
Action = Literal["Proceed", "Adjust", "Execute", "Revise"]

_ACTIONS = {"GOAL": {"Proceed", "Adjust"}, "PLAN": {"Execute", "Revise"}}
_EXECUTE = {"Proceed", "Execute"}
_READY = {"GOAL": "VECTOR_READY", "PLAN": "PLAN_APPROVED"}


class EntryError(ValueError):
    """Fail-closed entry validation error."""


@dataclass(frozen=True)
class EntryRequest:
    mode: Mode
    action: Action
    object_id: str
    revision: int
    expected_version: int
    actor: str
    policy_version: str
    source_commit: str
    idempotency_key: str


def _digest(request: EntryRequest) -> str:
    body = json.dumps(request.__dict__, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _check(request: EntryRequest, *, state: str, allowed: bool, gates: dict[str, str]) -> None:
    if request.mode not in _ACTIONS or request.action not in _ACTIONS[request.mode]:
        raise EntryError("ERROR_ENTRY_ACTION_MODE_MISMATCH")
    if any(not v or not isinstance(v, str) for v in (
        request.object_id, request.actor, request.policy_version,
        request.source_commit, request.idempotency_key
    )):
        raise EntryError("ERROR_ENTRY_MISSING_IDENTITY")
    if request.revision < 1 or request.expected_version < 1:
        raise EntryError("ERROR_ENTRY_INVALID_REVISION")
    if not allowed:
        raise EntryError("ERROR_ENTRY_UNAUTHORIZED")
    if request.action in _EXECUTE:
        if state != _READY[request.mode]:
            raise EntryError("ERROR_ENTRY_NOT_READY")
        if not gates or any(value != "PASS" for value in gates.values()):
            raise EntryError("ERROR_ENTRY_GATE_UNSATISFIED")
    elif state not in {_READY[request.mode], "VECTOR_CANDIDATE", "PLAN_CANDIDATE"}:
        raise EntryError("ERROR_ENTRY_REVISION_STATE")


class EntryStore:
    """SQLite transaction boundary; caller supplies an existing DB path.

    Entries record authorized intent, never execute external actions. Consumers
    must independently enforce work_run and individual tool-call permissions.
    """

    def __init__(self, database: str) -> None:
        self.database = database

    def initialize(self) -> None:
        with sqlite3.connect(self.database, timeout=30) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS execution_entry_objects (
                mode TEXT NOT NULL, object_id TEXT NOT NULL, revision INTEGER NOT NULL,
                version INTEGER NOT NULL, state TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (mode, object_id)
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS execution_entry_decisions (
                decision_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
                request_digest TEXT NOT NULL, mode TEXT NOT NULL, action TEXT NOT NULL,
                object_id TEXT NOT NULL, revision INTEGER NOT NULL,
                actor TEXT NOT NULL, source_commit TEXT NOT NULL, policy_version TEXT NOT NULL,
                result_state TEXT NOT NULL, receipt_json TEXT NOT NULL
            )""")

    def seed(self, *, mode: Mode, object_id: str, revision: int,
             version: int, state: str) -> None:
        """Bootstrap for explicit authoritative caller only, not exposed as a user endpoint."""
        if mode not in _ACTIONS or not object_id or revision < 1 or version < 1:
            raise EntryError("ERROR_ENTRY_SEED_INVALID")
        with sqlite3.connect(self.database, timeout=30, isolation_level=None) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO execution_entry_objects(mode,object_id,revision,version,state,active) "
                "VALUES(?,?,?,?,?,0)", (mode, object_id, revision, version, state)
            )
            conn.execute("COMMIT")

    def decide(self, request: EntryRequest, *, allowed: bool,
               gates: dict[str, str]) -> dict:
        fingerprint = _digest(request)
        conn = sqlite3.connect(self.database, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute(
                "SELECT * FROM execution_entry_decisions WHERE idempotency_key=?",
                (request.idempotency_key,)
            ).fetchone()
            if prior:
                if prior["request_digest"] != fingerprint:
                    raise EntryError("ERROR_ENTRY_IDEMPOTENCY_CONFLICT")
                conn.execute("COMMIT")
                return json.loads(prior["receipt_json"])
            row = conn.execute(
                "SELECT * FROM execution_entry_objects WHERE mode=? AND object_id=?",
                (request.mode, request.object_id)
            ).fetchone()
            if row is None:
                raise EntryError("ERROR_ENTRY_OBJECT_NOT_FOUND")
            if row["version"] != request.expected_version or row["revision"] != request.revision:
                raise EntryError("ERROR_ENTRY_STALE_REVISION")
            if row["active"]:
                raise EntryError("ERROR_ENTRY_ALREADY_ACTIVE")
            _check(request, state=row["state"], allowed=allowed, gates=gates)
            executing = request.action in _EXECUTE
            revision = request.revision if executing else request.revision + 1
            state = ("ENTRY_CLAIMED" if executing else
                     ("VECTOR_CANDIDATE" if request.mode == "GOAL" else "PLAN_CANDIDATE"))
            changed = conn.execute(
                "UPDATE execution_entry_objects SET revision=?,version=version+1,state=?,active=? "
                "WHERE mode=? AND object_id=? AND version=? AND revision=? AND active=0",
                (revision, state, int(executing), request.mode, request.object_id,
                 request.expected_version, request.revision)
            )
            if changed.rowcount != 1:
                raise EntryError("ERROR_ENTRY_CONCURRENT_MODIFICATION")
            receipt = {
                "decision_id": "entry_" + fingerprint[:24],
                "request_digest": fingerprint,
                "mode": request.mode, "action": request.action,
                "object_id": request.object_id, "previous_revision": request.revision,
                "result_revision": revision, "result_version": request.expected_version + 1,
                "result_state": state, "actor": request.actor,
                "policy_version": request.policy_version,
                "source_commit": request.source_commit,
                "dispatch_authorized": False,
            }
            conn.execute(
                "INSERT INTO execution_entry_decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (receipt["decision_id"], request.idempotency_key, fingerprint,
                 request.mode, request.action, request.object_id, request.revision,
                 request.actor, request.source_commit, request.policy_version,
                 state, json.dumps(receipt, sort_keys=True))
            )
            conn.execute("COMMIT")
            return receipt
        except Exception:
            conn.execute("ROLLBACK") if conn.in_transaction else None
            raise
        finally:
            conn.close()
