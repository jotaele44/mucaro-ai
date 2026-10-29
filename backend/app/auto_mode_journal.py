"""Offline append-only transaction journal with hash-chain integrity.

The journal records state transitions only. It never performs or undoes side effects.
Writers cooperate through a POSIX advisory lock; this is not a full rollback engine.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import fcntl
from contextlib import contextmanager
from pathlib import Path

from backend.app.auto_mode_scheduler import AutoModeError
from backend.app.auto_mode_transaction import Transaction, transition

MAX_BYTES = 2_000_000

def _token_ref(token: str, key: bytes) -> str:
    if not isinstance(key, bytes) or len(key) < 32:
        raise AutoModeError('JOURNAL_KEY_REQUIRED')
    if not isinstance(token, str):
        raise AutoModeError('INVALID_AUTHORIZATION_TOKEN')
    return hmac.new(key, token.encode('utf-8'), hashlib.sha256).hexdigest() if token else ''

def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")

def _digest(record: dict) -> str:
    return hashlib.sha256(_canonical(record)).hexdigest()

def _read(path: Path) -> list[dict]:
    if path.is_symlink():
        raise AutoModeError("INVALID_JOURNAL_PATH")
    if not path.exists():
        return []
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise AutoModeError("INVALID_JOURNAL")
    try:
        raw = path.read_bytes()
        if not raw:
            return []
        if not raw.endswith(b"\n"):
            raise ValueError("torn record")
        records = []
        previous = "0" * 64
        for line in raw.splitlines():
            record = json.loads(line)
            if not isinstance(record, dict) or set(record) != {"seq", "prev", "tx", "target", "token", "operation_id", "hash"}:
                raise ValueError("record shape")
            payload = {k: v for k, v in record.items() if k != "hash"}
            if type(record["seq"]) is not int or record["seq"] != len(records):
                raise ValueError("sequence")
            if record["prev"] != previous or not isinstance(record["hash"], str) or not hmac.compare_digest(record["hash"], _digest(payload)):
                raise ValueError("chain")
            if _canonical(record) != line:
                raise ValueError("noncanonical")
            previous = record["hash"]
            records.append(record)
        return records
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError) as exc:
        raise AutoModeError("INVALID_JOURNAL") from exc

def replay(path: Path, transaction_id: str, *, key: bytes) -> Transaction:
    _token_ref('', key)
    if not isinstance(transaction_id, str) or not transaction_id.strip():
        raise AutoModeError("INVALID_TRANSACTION")
    current = Transaction(transaction_id)
    for record in _read(Path(path)):
        if record["tx"] != transaction_id:
            raise AutoModeError("TRANSACTION_MISMATCH")
        current = transition(current, record["target"], token=record["token"], operation_id=record["operation_id"])
    return current

@contextmanager
def _exclusive_journal_lock(path: Path):
    """POSIX/macOS advisory lock on a stable sibling lockfile."""
    lock_path = path.with_name(path.name + ".lock")
    if lock_path.is_symlink():
        raise AutoModeError("INVALID_JOURNAL_PATH")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(lock_path, flags, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

def append_transition(path: Path, transaction_id: str, target: str, *, token: str = "", operation_id: str = "", key: bytes) -> Transaction:
    token_reference = _token_ref(token, key)
    path = Path(path)
    if path.is_symlink() or not path.parent.is_dir():
        raise AutoModeError("INVALID_JOURNAL_PATH")
    with _exclusive_journal_lock(path):
        records = _read(path)
        current = replay(path, transaction_id, key=key)
        updated = transition(current, target, token=token_reference, operation_id=operation_id)
        if updated == current:
            return current
        payload = {
            "seq": len(records), "prev": records[-1]["hash"] if records else "0" * 64,
            "tx": transaction_id, "target": target, "token": token_reference, "operation_id": operation_id,
        }
        record = dict(payload, hash=_digest(payload))
        encoded = _canonical(record) + b"\n"
        existing = path.read_bytes() if path.exists() else b""
        if len(existing) + len(encoded) > MAX_BYTES:
            raise AutoModeError("JOURNAL_FULL")
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        try:
            with os.fdopen(fd, "ab") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            pass
        return updated
