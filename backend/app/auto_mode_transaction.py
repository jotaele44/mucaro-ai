"""Offline Auto Mode transaction state machine; never executes external side effects."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

from backend.app.auto_mode_scheduler import AutoModeError

State = Literal["PENDING", "AUTHORIZED", "RUNNING", "PASS", "FAILED", "CANCELLED", "ROLLED_BACK"]

@dataclass(frozen=True)
class Transaction:
    id: str
    state: State = "PENDING"
    authorization_token: str = ""
    operation_id: str = ""
    history: tuple[str, ...] = ("PENDING",)

_ALLOWED = {
    "PENDING": {"AUTHORIZED", "CANCELLED"},
    "AUTHORIZED": {"RUNNING", "CANCELLED"},
    "RUNNING": {"PASS", "FAILED", "CANCELLED"},
    "PASS": {"ROLLED_BACK"},
    "FAILED": {"ROLLED_BACK"},
    "CANCELLED": {"ROLLED_BACK"},
    "ROLLED_BACK": set(),
}

def transition(tx: Transaction, target: State, *, token: str = "", operation_id: str = "") -> Transaction:
    if not isinstance(tx, Transaction) or not isinstance(tx.id, str) or not tx.id.strip():
        raise AutoModeError("INVALID_TRANSACTION")
    if tx.state not in _ALLOWED or not isinstance(tx.history, tuple) or not tx.history or tx.history[-1] != tx.state:
        raise AutoModeError("INVALID_TRANSACTION")
    if target not in _ALLOWED:
        raise AutoModeError("INVALID_TRANSITION")
    if target == tx.state:
        if target == "AUTHORIZED" and token != tx.authorization_token:
            raise AutoModeError("AUTHORIZATION_MISMATCH")
        if target == "RUNNING" and operation_id != tx.operation_id:
            raise AutoModeError("IDEMPOTENCY_CONFLICT")
        return tx
    if target not in _ALLOWED[tx.state]:
        raise AutoModeError("INVALID_TRANSITION")
    if target == "AUTHORIZED" and (not isinstance(token, str) or not token.strip()):
        raise AutoModeError("AUTHORIZATION_REQUIRED")
    if target == "RUNNING":
        if not isinstance(token, str) or token != tx.authorization_token:
            raise AutoModeError("AUTHORIZATION_MISMATCH")
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise AutoModeError("OPERATION_ID_REQUIRED")
    return Transaction(
        id=tx.id,
        state=target,
        authorization_token=token if target == "AUTHORIZED" else tx.authorization_token,
        operation_id=operation_id if target == "RUNNING" else tx.operation_id,
        history=tx.history + (target,),
    )
