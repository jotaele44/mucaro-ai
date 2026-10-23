"""Durable external-action intent and receipt journal for Mucaro V7.

Never executes external operations. Callers must bind a trusted executor that
honors idempotency_key and verifies the observed side effect. All stored data
is local SQLite state and does not establish independent immutable provenance.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
from .governed_learning_loop import digest

class OutboxGateError(RuntimeError):
    pass

class DurableDispatchOutbox:
    def __init__(self,path: str|Path):
        self.db=sqlite3.connect(str(path),isolation_level=None,timeout=5)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS dispatch_intents(
          intent_id TEXT PRIMARY KEY,
          action_digest TEXT NOT NULL,
          policy_digest TEXT NOT NULL,
          reservation_id TEXT NOT NULL,
          idempotency_key TEXT NOT NULL UNIQUE,
          state TEXT NOT NULL DEFAULT 'PENDING',
          result_digest TEXT,
          reconcile_count INTEGER NOT NULL DEFAULT 0
        )""")

    def declare(self,*,intent_id:str,action_digest:str,policy_digest:str,
                reservation_id:str,idempotency_key:str):
        if not all(isinstance(x,str) and x for x in
                   (intent_id,action_digest,policy_digest,reservation_id,idempotency_key)):
            raise OutboxGateError("MISSING_INTENT_IDENTITY")
        if not all(x.startswith("sha256:") for x in
                   (action_digest,policy_digest,idempotency_key)):
            raise OutboxGateError("UNVERIFIABLE_INTENT_IDENTITY")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            identity=(action_digest,policy_digest,reservation_id,idempotency_key)
            old=self.db.execute("SELECT action_digest,policy_digest,reservation_id,idempotency_key FROM dispatch_intents WHERE intent_id=?",(intent_id,)).fetchone()
            if old and tuple(old)!=identity:
                raise OutboxGateError("INTENT_IDENTITY_CONFLICT")
            if not old:
                self.db.execute("INSERT INTO dispatch_intents(intent_id,action_digest,policy_digest,reservation_id,idempotency_key) VALUES(?,?,?,?,?)",(intent_id,*identity))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def record_verified_effect(self,*,intent_id:str,action_digest:str,
                               result_digest:str,idempotency_key:str):
        if not isinstance(result_digest,str) or not result_digest.startswith("sha256:"):
            raise OutboxGateError("INVALID_EFFECT_RECEIPT")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old=self.db.execute("SELECT action_digest,idempotency_key,state,result_digest FROM dispatch_intents WHERE intent_id=?",(intent_id,)).fetchone()
            if not old or old[0]!=action_digest or old[1]!=idempotency_key:
                raise OutboxGateError("EFFECT_IDENTITY_CONFLICT")
            if old[2]=="VERIFIED":
                if old[3]!=result_digest:
                    raise OutboxGateError("EFFECT_RESULT_CONTRADICTION")
                self.db.execute("COMMIT")
                return
            if old[2]!="PENDING":
                raise OutboxGateError("INVALID_INTENT_STATE")
            self.db.execute("UPDATE dispatch_intents SET state='VERIFIED',result_digest=? WHERE intent_id=?",(result_digest,intent_id))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def reconcile(self,intent_id:str):
        """Recovery is read-only with respect to external actions."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row=self.db.execute("SELECT action_digest,policy_digest,reservation_id,idempotency_key,state,result_digest,reconcile_count FROM dispatch_intents WHERE intent_id=?",(intent_id,)).fetchone()
            if not row:
                raise OutboxGateError("INTENT_NOT_FOUND")
            self.db.execute("UPDATE dispatch_intents SET reconcile_count=reconcile_count+1 WHERE intent_id=?",(intent_id,))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return {"intent_id":intent_id,"action_digest":row[0],"policy_digest":row[1],
                "reservation_id":row[2],"idempotency_key":row[3],
                "status":"VERIFIED" if row[4]=="VERIFIED" else "REQUIRES_EXTERNAL_RECONCILIATION",
                "result_digest":row[5],"reconcile_count":row[6]+1}

    def close(self):
        self.db.close()
