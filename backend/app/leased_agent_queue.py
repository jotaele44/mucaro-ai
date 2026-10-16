"""Fenced, lease-based phase dispatch. No implicit tool, deployment or training authority.

At-least-once dispatch: external actions require executor-enforced idempotency.
Each claim and completion runs in a separate short SQLite transaction.
"""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from typing import Any
from .governed_learning_loop import PHASES, digest

class WorkerGateError(RuntimeError):
    pass

class LeasedAgentQueue:
    def __init__(self, path: str | Path):
        self.db = sqlite3.connect(str(path), isolation_level=None, timeout=5)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS jobs(
          job_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, phase TEXT NOT NULL,
          input_digest TEXT NOT NULL, policy_digest TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'READY', lease_owner TEXT,
          lease_until INTEGER, fence INTEGER NOT NULL DEFAULT 0,
          attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
          output_digest TEXT, cancelled INTEGER NOT NULL DEFAULT 0,
          UNIQUE(run_id,phase))""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS receipts(
          job_id TEXT NOT NULL, fence INTEGER NOT NULL, result_digest TEXT NOT NULL,
          PRIMARY KEY(job_id,fence))""")

    def submit(self, job_id: str, run_id: str, phase: str,
               input_digest: str, policy_digest: str, max_attempts: int = 3):
        if (not job_id or not run_id or phase not in PHASES or
            phase in ("PROMOTE", "MONITOR", "RECOVER") or
            not input_digest.startswith("sha256:") or
            not policy_digest.startswith("sha256:") or
            not 1 <= max_attempts <= 10):
            raise WorkerGateError("UNAUTHORIZED_OR_INVALID_JOB")
        with self.db:
            old = self.db.execute("SELECT run_id,phase,input_digest,policy_digest,max_attempts "
                                  "FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            identity = (run_id,phase,input_digest,policy_digest,max_attempts)
            if old:
                if tuple(old) != identity:
                    raise WorkerGateError("JOB_IDENTITY_CONFLICT")
                return
            duplicate = self.db.execute("SELECT job_id FROM jobs WHERE run_id=? AND phase=?",
                                        (run_id,phase)).fetchone()
            if duplicate:
                raise WorkerGateError("PHASE_ALREADY_QUEUED")
            self.db.execute("INSERT INTO jobs(job_id,run_id,phase,input_digest,policy_digest,"
                            "max_attempts) VALUES(?,?,?,?,?,?)",
                            (job_id,*identity))

    def claim(self, job_id: str, owner: str, now: int, lease_seconds: int,
              *, permitted: bool) -> dict[str, Any] | None:
        if not owner or not isinstance(now,int) or not 1 <= lease_seconds <= 3600:
            raise WorkerGateError("INVALID_LEASE")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT state,lease_until,fence,attempts,max_attempts,cancelled,"
                                  "run_id,phase,input_digest,policy_digest FROM jobs WHERE job_id=?",
                                  (job_id,)).fetchone()
            if not row:
                raise WorkerGateError("JOB_NOT_FOUND")
            state, until, fence, attempts, limit, cancelled, run, phase, inp, policy = row
            if cancelled or state in ("SUCCEEDED","FAILED","CANCELLED"):
                self.db.execute("COMMIT")
                return None
            if not permitted:
                self.db.execute("UPDATE jobs SET state='BLOCKED' WHERE job_id=?", (job_id,))
                self.db.execute("COMMIT")
                return None
            if state == "RUNNING" and until is not None and until > now:
                self.db.execute("COMMIT")
                return None
            if attempts >= limit:
                self.db.execute("UPDATE jobs SET state='FAILED' WHERE job_id=?", (job_id,))
                self.db.execute("COMMIT")
                return None
            fence += 1
            self.db.execute("UPDATE jobs SET state='RUNNING',lease_owner=?,lease_until=?,"
                            "fence=?,attempts=? WHERE job_id=?",
                            (owner,now+lease_seconds,fence,attempts+1,job_id))
            self.db.execute("COMMIT")
            return {"job_id":job_id,"run_id":run,"phase":phase,"input_digest":inp,
                    "policy_digest":policy,"owner":owner,"fence":fence,
                    "idempotency_key":digest([run,phase,inp,policy])}
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def finish(self, job_id: str, owner: str, fence: int, result: dict[str, Any],
               now: int) -> bool:
        if not isinstance(result,dict) or result.get("status") != "PASS":
            raise WorkerGateError("RESULT_NOT_VERIFIED")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT state,lease_owner,lease_until,fence,cancelled "
                                  "FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if not row or row[0] != "RUNNING" or row[1] != owner or row[3] != fence or row[4] or row[2] <= now:
                self.db.execute("COMMIT")
                return False
            h = digest(result)
            self.db.execute("INSERT INTO receipts(job_id,fence,result_digest) VALUES(?,?,?)",
                            (job_id,fence,h))
            self.db.execute("UPDATE jobs SET state='SUCCEEDED',output_digest=?,lease_until=NULL "
                            "WHERE job_id=?", (h,job_id))
            self.db.execute("COMMIT")
            return True
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def cancel(self, job_id: str):
        with self.db:
            self.db.execute("UPDATE jobs SET cancelled=1,state='CANCELLED',fence=fence+1,"
                            "lease_until=NULL WHERE job_id=? AND state NOT IN ('SUCCEEDED','FAILED')",
                            (job_id,))

    def state(self, job_id: str):
        row = self.db.execute("SELECT state,fence,attempts,output_digest,cancelled "
                              "FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return dict(zip(("state","fence","attempts","output_digest","cancelled"),row)) if row else None

    def close(self):
        self.db.close()
