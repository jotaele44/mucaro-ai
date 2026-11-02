"""Durable aggregate resource budget accounting for bounded Mucaro agent runs.

This ledger tracks explicitly *declared units*, not independently metered costs.
Reservations are exclusive within a SQLite database; rollback/reconciliation of
external provider charges requires separate executor evidence.
"""
from __future__ import annotations
import math
import sqlite3
from pathlib import Path

class BudgetGateError(RuntimeError):
    pass

def _units(n: float, *, allow_zero: bool = False) -> float:
    if isinstance(n,bool) or not isinstance(n,(int,float)) or not math.isfinite(n) or n < 0 or (n == 0 and not allow_zero):
        raise BudgetGateError("INVALID_RESOURCE_UNITS")
    return float(n)

class AggregateBudgetLedger:
    def __init__(self, path: str | Path):
        self.db=sqlite3.connect(str(path),isolation_level=None,timeout=5)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS budget_accounts(
            account_id TEXT PRIMARY KEY, limit_units REAL NOT NULL,
            reserved_units REAL NOT NULL DEFAULT 0,
            spent_units REAL NOT NULL DEFAULT 0)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS budget_reservations(
            reservation_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
            bound_action_digest TEXT NOT NULL, units REAL NOT NULL,
            state TEXT NOT NULL DEFAULT 'RESERVED',
            spent_units REAL NOT NULL DEFAULT 0,
            FOREIGN KEY(account_id) REFERENCES budget_accounts(account_id))""")

    def open_account(self, account_id: str, limit_units: float):
        limit=_units(limit_units)
        if not isinstance(account_id,str) or not account_id.strip():
            raise BudgetGateError("INVALID_ACCOUNT")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old=self.db.execute("SELECT limit_units FROM budget_accounts WHERE account_id=?",(account_id,)).fetchone()
            if old and old[0]!=limit:
                raise BudgetGateError("BUDGET_LIMIT_CONFLICT")
            if not old:
                self.db.execute("INSERT INTO budget_accounts(account_id,limit_units) VALUES(?,?)",(account_id,limit))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def reserve(self, *, account_id: str, reservation_id: str,
                action_digest: str, units: float) -> bool:
        amount=_units(units)
        if not reservation_id or not isinstance(action_digest,str) or not action_digest.startswith("sha256:"):
            raise BudgetGateError("INVALID_RESERVATION_IDENTITY")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old=self.db.execute("SELECT account_id,bound_action_digest,units,state FROM budget_reservations WHERE reservation_id=?",(reservation_id,)).fetchone()
            if old:
                if old[:3]!=(account_id,action_digest,amount):
                    raise BudgetGateError("RESERVATION_IDENTITY_CONFLICT")
                self.db.execute("COMMIT")
                return old[3]=="RESERVED"
            account=self.db.execute("SELECT limit_units,reserved_units,spent_units FROM budget_accounts WHERE account_id=?",(account_id,)).fetchone()
            if not account:
                raise BudgetGateError("ACCOUNT_NOT_FOUND")
            if account[1]+account[2]+amount > account[0]+1e-10:
                self.db.execute("COMMIT")
                return False
            self.db.execute("INSERT INTO budget_reservations(reservation_id,account_id,bound_action_digest,units) VALUES(?,?,?,?)",(reservation_id,account_id,action_digest,amount))
            self.db.execute("UPDATE budget_accounts SET reserved_units=reserved_units+? WHERE account_id=?",(amount,account_id))
            self.db.execute("COMMIT")
            return True
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def settle(self, reservation_id: str, *, actual_units: float, action_digest: str) -> bool:
        actual=_units(actual_units,allow_zero=True)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old=self.db.execute("SELECT account_id,bound_action_digest,units,state,spent_units FROM budget_reservations WHERE reservation_id=?",(reservation_id,)).fetchone()
            if not old or old[1]!=action_digest:
                raise BudgetGateError("RESERVATION_NOT_FOUND_OR_MISMATCH")
            account,_,reserved,state,spent=old
            if actual>reserved:
                raise BudgetGateError("OBSERVED_COST_EXCEEDS_RESERVATION")
            if state=="SETTLED":
                if spent!=actual:
                    raise BudgetGateError("SETTLEMENT_CONFLICT")
                self.db.execute("COMMIT")
                return True
            if state!="RESERVED":
                raise BudgetGateError("TERMINAL_RESERVATION")
            self.db.execute("UPDATE budget_accounts SET reserved_units=reserved_units-?,spent_units=spent_units+? WHERE account_id=?",(reserved,actual,account))
            self.db.execute("UPDATE budget_reservations SET state='SETTLED',spent_units=? WHERE reservation_id=?",(actual,reservation_id))
            self.db.execute("COMMIT")
            return True
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def release_unexecuted(self, reservation_id: str, *, action_digest: str) -> bool:
        """Releases only a reservation whose executor has independently proven no effect.

        Callers are responsible for providing that proof; the ledger alone
        cannot determine whether an external operation already ran.
        """
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row=self.db.execute("SELECT account_id,bound_action_digest,units,state FROM budget_reservations WHERE reservation_id=?",(reservation_id,)).fetchone()
            if not row or row[1]!=action_digest:
                raise BudgetGateError("RESERVATION_NOT_FOUND_OR_MISMATCH")
            if row[3]=="RELEASED":
                self.db.execute("COMMIT")
                return True
            if row[3]!="RESERVED":
                raise BudgetGateError("TERMINAL_RESERVATION")
            self.db.execute("UPDATE budget_accounts SET reserved_units=reserved_units-? WHERE account_id=?",(row[2],row[0]))
            self.db.execute("UPDATE budget_reservations SET state='RELEASED' WHERE reservation_id=?",(reservation_id,))
            self.db.execute("COMMIT")
            return True
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def summary(self, account_id: str):
        row=self.db.execute("SELECT limit_units,reserved_units,spent_units FROM budget_accounts WHERE account_id=?",(account_id,)).fetchone()
        if not row:
            raise BudgetGateError("ACCOUNT_NOT_FOUND")
        return dict(zip(("limit_units","reserved_units","spent_units"),row))

    def close(self):
        self.db.close()
