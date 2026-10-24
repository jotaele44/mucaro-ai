from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .capability_policy import KNOWN_CAPABILITIES
from .config import Settings, settings
from .db import get_connection, init_db
from .utils import stable_id, utc_now

PERMISSION_LEASE_SCHEMA_VERSION = "mucaro-permission-lease/1.0"
LEASE_STATES = ("ACTIVE", "EXHAUSTED", "EXPIRED", "REVOKED")
MAX_TTL_SECONDS = 3600
MAX_USES = 100


class PermissionLeaseError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _text(value: Any, label: str, *, max_length: int = 160) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > max_length:
        raise PermissionLeaseError(f"ERROR_LEASE_{label.upper()}_INVALID", f"{label} must be a non-empty string of at most {max_length} characters")
    return value.strip()


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise PermissionLeaseError("ERROR_LEASE_TIME_INVALID", "lease timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise PermissionLeaseError("ERROR_LEASE_TIME_INVALID", "lease timestamp must include timezone")
    return parsed.astimezone(UTC)


def _validate_payload(lease: dict[str, Any]) -> None:
    if lease.get("schema_version") != PERMISSION_LEASE_SCHEMA_VERSION:
        raise PermissionLeaseError("ERROR_LEASE_SCHEMA_INVALID", "stored permission lease schema version is unsupported")
    _text(lease.get("lease_id"), "lease_id")
    _text(lease.get("run_id"), "run_id")
    _text(lease.get("capability"), "capability")
    if lease["capability"] not in KNOWN_CAPABILITIES:
        raise PermissionLeaseError("ERROR_LEASE_CAPABILITY_UNKNOWN", lease["capability"])
    _text(lease.get("subject"), "subject")
    if lease.get("target") is not None and not isinstance(lease.get("target"), str):
        raise PermissionLeaseError("ERROR_LEASE_TARGET_INVALID", "target must be a string or null")
    if lease.get("state") not in LEASE_STATES:
        raise PermissionLeaseError("ERROR_LEASE_STATE_INVALID", str(lease.get("state")))
    if not isinstance(lease.get("uses"), int) or lease["uses"] < 0:
        raise PermissionLeaseError("ERROR_LEASE_USES_INVALID", "uses must be a non-negative integer")
    if not isinstance(lease.get("max_uses"), int) or not 1 <= lease["max_uses"] <= MAX_USES:
        raise PermissionLeaseError("ERROR_LEASE_MAX_USES_INVALID", "max_uses must be between 1 and 100")
    if lease["uses"] > lease["max_uses"]:
        raise PermissionLeaseError("ERROR_LEASE_USES_INVALID", "uses cannot exceed max_uses")
    _parse_time(lease.get("created_at"))
    _parse_time(lease.get("expires_at"))
    if lease.get("revoked_at") is not None:
        _parse_time(lease["revoked_at"])


def _decode_rows(rows: list[Any]) -> list[dict[str, Any]]:
    leases: list[dict[str, Any]] = []
    for row in rows:
        try:
            lease = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise PermissionLeaseError("ERROR_LEASE_CORRUPT", "stored permission lease is not valid JSON") from exc
        if not isinstance(lease, dict):
            raise PermissionLeaseError("ERROR_LEASE_CORRUPT", "stored permission lease is not an object")
        _validate_payload(lease)
        leases.append(lease)
    return leases


def _effective_state(lease: dict[str, Any], now: datetime | None = None) -> str:
    if lease["state"] in {"REVOKED", "EXHAUSTED"}:
        return lease["state"]
    if lease["uses"] >= lease["max_uses"]:
        return "EXHAUSTED"
    if (now or datetime.now(UTC)) >= _parse_time(lease["expires_at"]):
        return "EXPIRED"
    return "ACTIVE"


def create_permission_lease(
    run_id: str,
    capability: str,
    subject: str,
    *,
    target: str | None = None,
    ttl_seconds: int = 300,
    max_uses: int = 1,
    granted_by: str = "operator",
    cfg: Settings = settings,
) -> dict[str, Any]:
    run_id = _text(run_id, "run_id")
    capability = _text(capability, "capability")
    subject = _text(subject, "subject")
    granted_by = _text(granted_by, "granted_by")
    if capability not in KNOWN_CAPABILITIES:
        raise PermissionLeaseError("ERROR_LEASE_CAPABILITY_UNKNOWN", capability)
    if target is not None and not isinstance(target, str):
        raise PermissionLeaseError("ERROR_LEASE_TARGET_INVALID", "target must be a string or null")
    if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or not 1 <= ttl_seconds <= MAX_TTL_SECONDS:
        raise PermissionLeaseError("ERROR_LEASE_TTL_INVALID", "ttl_seconds must be between 1 and 3600")
    if not isinstance(max_uses, int) or isinstance(max_uses, bool) or not 1 <= max_uses <= MAX_USES:
        raise PermissionLeaseError("ERROR_LEASE_MAX_USES_INVALID", "max_uses must be between 1 and 100")
    created_at = utc_now()
    expires_at = (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat().replace("+00:00", "Z")
    lease = {
        "schema_version": PERMISSION_LEASE_SCHEMA_VERSION,
        "lease_id": stable_id("lease", run_id, capability, subject, target or "", created_at, length=16),
        "run_id": run_id,
        "capability": capability,
        "subject": subject,
        "target": target,
        "state": "ACTIVE",
        "created_at": created_at,
        "expires_at": expires_at,
        "revoked_at": None,
        "uses": 0,
        "max_uses": max_uses,
        "granted_by": granted_by,
        "sequence": 0,
    }
    _validate_payload(lease)
    init_db(cfg.sqlite_path)
    conn = get_connection(cfg.sqlite_path)
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO permission_leases (lease_id, payload_json) VALUES (?, ?)",
            (lease["lease_id"], json.dumps(lease, ensure_ascii=False, sort_keys=True)),
        )
        conn.execute("COMMIT")
        return lease
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def list_permission_leases(cfg: Settings = settings, *, run_id: str | None = None) -> list[dict[str, Any]]:
    init_db(cfg.sqlite_path)
    with get_connection(cfg.sqlite_path) as conn:
        rows = conn.execute("SELECT payload_json FROM permission_leases ORDER BY rowid ASC").fetchall()
    leases = _decode_rows(rows)
    now = datetime.now(UTC)
    for lease in leases:
        lease["effective_state"] = _effective_state(lease, now)
    if run_id is not None:
        run_id = _text(run_id, "run_id")
        leases = [lease for lease in leases if lease["run_id"] == run_id]
    return leases


def inspect_permission_lease(
    lease_id: str,
    *,
    run_id: str,
    capability: str,
    subject: str,
    target: str | None = None,
    cfg: Settings = settings,
) -> dict[str, Any]:
    lease_id = _text(lease_id, "lease_id")
    run_id = _text(run_id, "run_id")
    capability = _text(capability, "capability")
    subject = _text(subject, "subject")
    leases = list_permission_leases(cfg)
    lease = next((item for item in leases if item["lease_id"] == lease_id), None)
    if lease is None:
        raise PermissionLeaseError("ERROR_LEASE_NOT_FOUND", lease_id)
    if (lease["run_id"], lease["capability"], lease["subject"], lease.get("target")) != (run_id, capability, subject, target):
        raise PermissionLeaseError("ERROR_LEASE_BINDING_MISMATCH", "lease identity does not match the execution request")
    if lease["effective_state"] != "ACTIVE":
        raise PermissionLeaseError(f"ERROR_LEASE_{lease['effective_state']}", f"lease is {lease['effective_state']}")
    return lease


def consume_permission_lease(
    lease_id: str,
    *,
    run_id: str,
    capability: str,
    subject: str,
    target: str | None = None,
    cfg: Settings = settings,
) -> dict[str, Any]:
    lease_id = _text(lease_id, "lease_id")
    run_id = _text(run_id, "run_id")
    capability = _text(capability, "capability")
    subject = _text(subject, "subject")
    init_db(cfg.sqlite_path)
    conn = get_connection(cfg.sqlite_path)
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT payload_json FROM permission_leases WHERE lease_id = ?", (lease_id,)).fetchone()
        if row is None:
            raise PermissionLeaseError("ERROR_LEASE_NOT_FOUND", lease_id)
        lease = _decode_rows([row])[0]
        if (lease["run_id"], lease["capability"], lease["subject"], lease.get("target")) != (run_id, capability, subject, target):
            raise PermissionLeaseError("ERROR_LEASE_BINDING_MISMATCH", "lease identity does not match the execution request")
        effective = _effective_state(lease)
        if effective != "ACTIVE":
            if effective == "EXPIRED" and lease["state"] != "EXPIRED":
                lease["state"] = "EXPIRED"
                lease["sequence"] += 1
                conn.execute("UPDATE permission_leases SET payload_json = ? WHERE lease_id = ?", (json.dumps(lease, sort_keys=True), lease_id))
                conn.execute("COMMIT")
            raise PermissionLeaseError(f"ERROR_LEASE_{effective}", f"lease is {effective}")
        lease["uses"] += 1
        lease["state"] = "EXHAUSTED" if lease["uses"] >= lease["max_uses"] else "ACTIVE"
        lease["sequence"] += 1
        conn.execute("UPDATE permission_leases SET payload_json = ? WHERE lease_id = ?", (json.dumps(lease, ensure_ascii=False, sort_keys=True), lease_id))
        conn.execute("COMMIT")
        lease["effective_state"] = lease["state"]
        return lease
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def revoke_permission_lease(lease_id: str, cfg: Settings = settings) -> dict[str, Any]:
    lease_id = _text(lease_id, "lease_id")
    init_db(cfg.sqlite_path)
    conn = get_connection(cfg.sqlite_path)
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT payload_json FROM permission_leases WHERE lease_id = ?", (lease_id,)).fetchone()
        if row is None:
            raise PermissionLeaseError("ERROR_LEASE_NOT_FOUND", lease_id)
        lease = _decode_rows([row])[0]
        if lease["state"] not in {"REVOKED", "EXHAUSTED"}:
            lease["state"] = "REVOKED"
            lease["revoked_at"] = utc_now()
            lease["sequence"] += 1
            conn.execute("UPDATE permission_leases SET payload_json = ? WHERE lease_id = ?", (json.dumps(lease, ensure_ascii=False, sort_keys=True), lease_id))
        conn.execute("COMMIT")
        return lease
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
