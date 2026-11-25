"""One hash-chained stream for source acquisition and permission changes (TGT-035).

Tool calls have their own chained audit (`tools.py`) and permission leases theirs (`permission_leases.py`). What
was missing is a single record of what entered the library and what Mucaro was allowed to do: sources ingested or
removed, web snapshots persisted, folder grants and revocations, lease grants and revocations, and model settings
changes. Each event records identifiers, hashes and counts only. It never records document text, a secret such as an
API key, or a full web address (only its host), so the stream can be read to diagnose a problem without exposing
private content.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .config import Settings, settings
from .contracts import APPLICATION_ROOT, validate_contract
from .utils import canonical_sha256, read_json, stable_id, update_json, utc_now

GOVERNANCE_AUDIT_NAME = "governance_audit_log.json"

EVENT_KINDS = (
    "SOURCE_INGESTED",
    "SOURCE_DELETED",
    "WEB_SNAPSHOT_PERSISTED",
    "DIRECTORY_GRANTED",
    "DIRECTORY_REVOKED",
    "LEASE_GRANTED",
    "LEASE_REVOKED",
    "MODEL_SETTINGS_CHANGED",
)

# Keys that may never appear in an event, at any depth. A caller that passes one is a bug, so it is refused.
FORBIDDEN_KEYS = frozenset({"api_key", "text", "content", "text_preview", "chunks", "token", "password", "secret"})


class GovernanceAuditError(ValueError):
    def __init__(self, status: str, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def governance_audit_path(cfg: Settings) -> Path:
    return cfg.processed_dir / GOVERNANCE_AUDIT_NAME


def url_host(url: str | None) -> str:
    """The host of a web address, without path, query or credentials."""
    try:
        return (urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def _forbidden_keys(value: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            here = f"{path}.{key}" if path else str(key)
            if str(key).lower() in FORBIDDEN_KEYS:
                found.append(here)
            found.extend(_forbidden_keys(item, here))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_forbidden_keys(item, f"{path}[{index}]"))
    return found


def record_governance_event(event: str, actor: str, details: dict[str, Any], cfg: Settings = settings) -> dict[str, Any]:
    """Append one chained event under an exclusive lock and return it."""
    if event not in EVENT_KINDS:
        raise GovernanceAuditError("ERROR_GOVERNANCE_EVENT_UNKNOWN", event)
    leaked = _forbidden_keys(details)
    if leaked:
        raise GovernanceAuditError("ERROR_GOVERNANCE_EVENT_PRIVATE_FIELD", ",".join(leaked))
    cfg.ensure_dirs()

    def append(events: list[dict[str, Any]]) -> dict[str, Any]:
        previous = next((row.get("event_sha256") for row in reversed(events) if isinstance(row, dict) and row.get("event_sha256")), None)
        created_at = utc_now()
        payload = {
            "event_id": stable_id("govevent", created_at, event, str(len(events)), canonical_sha256(details)),
            "event": event,
            "actor": actor or "unknown",
            "created_at": created_at,
            "details": details,
            "previous_event_sha256": previous,
        }
        payload["event_sha256"] = canonical_sha256(payload)
        validate_contract("governance_audit_event", payload, root=APPLICATION_ROOT)
        events.append(payload)
        return payload

    return update_json(governance_audit_path(cfg), [], append)


def load_governance_events(cfg: Settings = settings) -> list[dict[str, Any]]:
    events = read_json(governance_audit_path(cfg), [])
    return [event for event in events if isinstance(event, dict)] if isinstance(events, list) else []


def verify_governance_audit_chain(cfg: Settings = settings) -> dict[str, Any]:
    """Detect edited, removed, inserted or reordered events."""
    raw = read_json(governance_audit_path(cfg), [])
    if not isinstance(raw, list):
        return {"status": "GOVERNANCE_AUDIT_CHAIN_BROKEN", "index": None, "reason": "LOG_NOT_A_LIST"}
    previous: str | None = None
    for index, event in enumerate(raw):
        if not isinstance(event, dict) or not event.get("event_sha256"):
            return {"status": "GOVERNANCE_AUDIT_CHAIN_BROKEN", "index": index, "reason": "UNHASHED_EVENT"}
        body = {key: value for key, value in event.items() if key != "event_sha256"}
        if canonical_sha256(body) != event["event_sha256"]:
            return {"status": "GOVERNANCE_AUDIT_CHAIN_BROKEN", "index": index, "reason": "EVENT_HASH_MISMATCH"}
        if event.get("previous_event_sha256") != previous:
            return {"status": "GOVERNANCE_AUDIT_CHAIN_BROKEN", "index": index, "reason": "PREVIOUS_HASH_MISMATCH"}
        previous = event["event_sha256"]
    return {"status": "GOVERNANCE_AUDIT_CHAIN_OK", "event_count": len(raw), "head_sha256": previous}


def list_governance_events(cfg: Settings = settings, *, event: str | None = None, limit: int | None = None) -> dict[str, Any]:
    events = load_governance_events(cfg)
    if event:
        events = [row for row in events if row.get("event") == event]
    if limit is not None and limit >= 0:
        events = events[-limit:] if limit else []
    return {"status": "GOVERNANCE_AUDIT_OK", "events": events, "chain": verify_governance_audit_chain(cfg)}
