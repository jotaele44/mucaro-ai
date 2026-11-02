"""Crash-restartable, policy-gated agent step engine.

Single-process SQLite coordinator; tool invocation is injected, never inferred.
Steps are at-least-once unless executor supplies idempotent external receipts.
No production promotion, tool-policy expansion, or model-weight training here.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Callable, Any

from .governed_learning_loop import PHASES, digest

SCHEMA = "mucaro-agent-run/1.0"
BLOCKED_PHASES = frozenset({"PROMOTE", "MONITOR", "RECOVER"})
TERMINAL = frozenset({"SUCCEEDED", "FAILED", "BLOCKED"})


class AgentRunError(RuntimeError):
    pass


def _payload(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


class AgentRunStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self.db = sqlite3.connect(self.path, isolation_level=None)
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS agent_runs(
            run_id TEXT PRIMARY KEY, objective TEXT NOT NULL,
            input_digest TEXT NOT NULL, status TEXT NOT NULL,
            phase_cursor INTEGER NOT NULL DEFAULT 0,
            policy_digest TEXT NOT NULL, executor_digest TEXT NOT NULL
          );
          CREATE TABLE IF NOT EXISTS agent_steps(
            run_id TEXT NOT NULL, phase_index INTEGER NOT NULL,
            state TEXT NOT NULL, output_digest TEXT, output_json TEXT,
            error TEXT, attempt INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(run_id, phase_index),
            FOREIGN KEY(run_id) REFERENCES agent_runs(run_id)
          );
          CREATE TABLE IF NOT EXISTS agent_events(
            run_id TEXT NOT NULL, sequence INTEGER NOT NULL,
            event_json TEXT NOT NULL, event_hash TEXT NOT NULL,
            PRIMARY KEY(run_id, sequence)
          );
        """)

    def create(self, run_id: str, objective: str, input_digest: str,
               policy_digest: str, executor_digest: str) -> None:
        if not all(isinstance(s, str) and s.strip() for s in (
                run_id, objective, input_digest, policy_digest, executor_digest)):
            raise AgentRunError("IDENTITY_REQUIRED")
        with self.db:
            row = self.db.execute(
                "SELECT objective,input_digest,policy_digest,executor_digest FROM agent_runs "
                "WHERE run_id=?", (run_id,)).fetchone()
            identity = (objective, input_digest, policy_digest, executor_digest)
            if row:
                if tuple(row) != identity:
                    raise AgentRunError("RUN_IDENTITY_CONFLICT")
                return
            self.db.execute(
                "INSERT INTO agent_runs(run_id,objective,input_digest,status,policy_digest,executor_digest) "
                "VALUES(?,?,?,'OPEN',?,?)", (run_id, *identity))

    def _append(self, run_id: str, event: dict[str, Any]) -> None:
        rows = self.db.execute(
            "SELECT sequence,event_hash FROM agent_events WHERE run_id=? "
            "ORDER BY sequence DESC LIMIT 1", (run_id,)).fetchone()
        seq, prior = ((rows[0] + 1, rows[1]) if rows else (1, None))
        obj = {"run_id": run_id, "sequence": seq, "prev_hash": prior, **event}
        self.db.execute("INSERT INTO agent_events VALUES(?,?,?,?)",
                        (run_id, seq, _payload(obj), digest(obj)))

    def advance(self, run_id: str, *, policy_check: Callable[[str], bool],
                execute: Callable[[str, str], dict[str, Any]],
                allow_promotion: bool = False) -> dict[str, Any]:
        """One terminal phase per call; completed steps are never executed twice."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT status,phase_cursor FROM agent_runs WHERE run_id=?",
                (run_id,)).fetchone()
            if row is None:
                raise AgentRunError("RUN_NOT_FOUND")
            status, cursor = row
            if status in TERMINAL or cursor == len(PHASES):
                self.db.execute("COMMIT")
                return {"status": status, "next_phase": None}
            phase = PHASES[cursor]
            if phase in BLOCKED_PHASES or (phase == "PROMOTE" and not allow_promotion):
                state, output, error = "BLOCKED", None, "REQUIRES_SEPARATE_RUNTIME"
            elif not policy_check(phase):
                state, output, error = "BLOCKED", None, "POLICY_DENIED"
            else:
                # Execution is intentionally outside the database transaction in
                # production; V1 keeps it here to prevent simultaneous local claims.
                # A crash after external side effects and before commit can replay
                # the step. Executors MUST be idempotent by (run_id, phase).
                try:
                    output = execute(run_id, phase)
                    if not isinstance(output, dict) or output.get("status") != "PASS":
                        state, error = "FAILED", "EXECUTOR_NOT_PASS"
                    else:
                        state, error = "SUCCEEDED", None
                except Exception as exc:
                    state, output, error = "FAILED", None, type(exc).__name__
            self.db.execute(
                "INSERT INTO agent_steps(run_id,phase_index,state,output_digest,output_json,error,attempt)"
                " VALUES(?,?,?,?,?,?,1)",
                (run_id, cursor, state, digest(output) if output is not None else None,
                 _payload(output) if output is not None else None, error))
            self._append(run_id, {"phase": phase, "state": state,
                                  "output_digest": digest(output) if output is not None else None,
                                  "error": error})
            new_status = ("BLOCKED" if state == "BLOCKED" else
                          "FAILED" if state == "FAILED" else "OPEN")
            self.db.execute(
                "UPDATE agent_runs SET status=?,phase_cursor=? WHERE run_id=?",
                (new_status, cursor + (1 if state == "SUCCEEDED" else 0), run_id))
            self.db.execute("COMMIT")
            return {"phase": phase, "state": state, "next_phase":
                    PHASES[cursor + 1] if state == "SUCCEEDED" and cursor + 1 < len(PHASES) else None}
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def receipt(self, run_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM agent_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise AgentRunError("RUN_NOT_FOUND")
        events = [{"event": json.loads(e), "event_hash": h} for e, h in
                  self.db.execute("SELECT event_json,event_hash FROM agent_events "
                                  "WHERE run_id=? ORDER BY sequence", (run_id,))]
        out = {"schema": SCHEMA, "identity": list(row), "events": events}
        out["receipt_hash"] = digest(out)
        return out

    def close(self) -> None:
        self.db.close()
