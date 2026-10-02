from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jsonschema

from .config import Settings, settings
from .tools import (
    TOOL_REGISTRY,
    ToolRuntimeError,
    approve_tool_call,
    arguments_digest,
    list_tool_calls,
)
from .utils import canonical_sha256, read_json, stable_id, update_json, utc_now

LEASE_SCHEMA_VERSION = "mucaro-permission-lease/1.0"
LEASES_NAME = "permission_leases.json"
LEASE_AUDIT_NAME = "permission_lease_audit.json"
SCOPE_TYPES = {"RUN_READ_ONLY", "EXACT_TOOLS", "TOOL_PREFIXES"}
MAX_TTL_SECONDS = 86_400
MAX_USES = 1000


class PermissionLeaseError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "schemas" / "permission_lease.schema.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PermissionLeaseError("ERROR_PERMISSION_LEASE_SCHEMA_UNAVAILABLE", f"{path}: {exc}") from exc


def _leases_path(cfg: Settings) -> Path:
    return cfg.processed_dir / LEASES_NAME


def _audit_path(cfg: Settings) -> Path:
    return cfg.processed_dir / LEASE_AUDIT_NAME


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise PermissionLeaseError("ERROR_PERMISSION_LEASE_TIME", f"invalid timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise PermissionLeaseError("ERROR_PERMISSION_LEASE_TIME", f"timestamp has no timezone: {value}")
    return parsed.astimezone(UTC)


def _grant_payload(lease: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": lease["schema_version"],
        "lease_id": lease["lease_id"],
        "run_id": lease["run_id"],
        "scope_type": lease["scope_type"],
        "tool_names": list(lease["tool_names"]),
        "tool_prefixes": list(lease["tool_prefixes"]),
        "workspace_root": lease["workspace_root"],
        "allow_mutating": lease["allow_mutating"],
        "allow_network": lease["allow_network"],
        "granted_by": lease["granted_by"],
        "created_at": lease["created_at"],
        "expires_at": lease["expires_at"],
        "max_uses": lease["max_uses"],
    }


def _validate_schema(lease: dict[str, Any]) -> None:
    errors = sorted(jsonschema.Draft202012Validator(_schema()).iter_errors(lease), key=lambda e: list(e.absolute_path))
    if errors:
        where = "/".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise PermissionLeaseError("ERROR_PERMISSION_LEASE_SCHEMA", f"{where}: {errors[0].message}")


def _append_audit(event: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    cfg.ensure_dirs()

    def append(events: list[dict[str, Any]]) -> dict[str, Any]:
        previous = next((row.get("event_sha256") for row in reversed(events) if row.get("event_sha256")), None)
        created_at = utc_now()
        payload = {
            "event_id": stable_id(
                "leaseevent",
                str(event.get("lease_id") or ""),
                str(event.get("event") or ""),
                created_at,
                str(len(events)),
            ),
            "created_at": created_at,
            **event,
            "previous_event_sha256": previous,
        }
        payload["event_sha256"] = canonical_sha256(payload)
        events.append(payload)
        return payload

    return update_json(_audit_path(cfg), [], append)


def verify_permission_lease_audit_chain(cfg: Settings = settings) -> dict[str, Any]:
    events = read_json(_audit_path(cfg), [])
    if not isinstance(events, list):
        return {"status": "PERMISSION_LEASE_AUDIT_BROKEN", "reason": "AUDIT_NOT_ARRAY"}
    previous: str | None = None
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            return {"status": "PERMISSION_LEASE_AUDIT_BROKEN", "index": index, "reason": "EVENT_NOT_OBJECT"}
        digest = event.get("event_sha256")
        if not isinstance(digest, str):
            return {"status": "PERMISSION_LEASE_AUDIT_BROKEN", "index": index, "reason": "EVENT_HASH_MISSING"}
        body = {key: value for key, value in event.items() if key != "event_sha256"}
        if canonical_sha256(body) != digest:
            return {"status": "PERMISSION_LEASE_AUDIT_BROKEN", "index": index, "reason": "EVENT_HASH_MISMATCH"}
        if event.get("previous_event_sha256") != previous:
            return {"status": "PERMISSION_LEASE_AUDIT_BROKEN", "index": index, "reason": "PREVIOUS_HASH_MISMATCH"}
        previous = digest
    return {"status": "PERMISSION_LEASE_AUDIT_OK", "event_count": len(events)}


def _grant_event(lease_id: str, cfg: Settings) -> dict[str, Any] | None:
    events = read_json(_audit_path(cfg), [])
    rows = [
        event
        for event in events
        if isinstance(event, dict)
        and event.get("event") == "LEASE_GRANTED"
        and event.get("lease_id") == lease_id
    ]
    if len(rows) > 1:
        raise PermissionLeaseError("ERROR_PERMISSION_LEASE_GRANT_AMBIGUOUS", lease_id)
    return rows[0] if rows else None


def _validate_integrity(lease: dict[str, Any], cfg: Settings) -> None:
    _validate_schema(lease)
    expected = canonical_sha256(_grant_payload(lease))
    if lease.get("grant_sha256") != expected:
        raise PermissionLeaseError("ERROR_PERMISSION_LEASE_GRANT_HASH", lease["lease_id"])
    audit = verify_permission_lease_audit_chain(cfg)
    if audit["status"] != "PERMISSION_LEASE_AUDIT_OK":
        raise PermissionLeaseError("ERROR_PERMISSION_LEASE_AUDIT_BROKEN", str(audit))
    grant = _grant_event(lease["lease_id"], cfg)
    if grant is None:
        raise PermissionLeaseError("ERROR_PERMISSION_LEASE_GRANT_EVENT_MISSING", lease["lease_id"])
    if grant.get("grant_sha256") != lease["grant_sha256"] or grant.get("run_id") != lease["run_id"]:
        raise PermissionLeaseError("ERROR_PERMISSION_LEASE_GRANT_EVENT_MISMATCH", lease["lease_id"])


def _canonical_workspace(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PermissionLeaseError("ERROR_PERMISSION_LEASE_WORKSPACE", "workspace_root must be a non-empty path or null")
    return str(Path(value).expanduser().resolve())


def _validate_scope(
    *,
    scope_type: str,
    tool_names: list[str],
    tool_prefixes: list[str],
    allow_mutating: bool,
) -> None:
    if scope_type not in SCOPE_TYPES:
        raise PermissionLeaseError("ERROR_PERMISSION_LEASE_SCOPE", str(scope_type))
    if not all(isinstance(name, str) and name for name in tool_names):
        raise PermissionLeaseError("ERROR_PERMISSION_LEASE_TOOL_NAMES", "tool_names must be non-empty strings")
    if not all(isinstance(prefix, str) and prefix and not prefix.startswith(".") for prefix in tool_prefixes):
        raise PermissionLeaseError("ERROR_PERMISSION_LEASE_TOOL_PREFIXES", "tool_prefixes are invalid")
    if len(tool_names) != len(set(tool_names)) or len(tool_prefixes) != len(set(tool_prefixes)):
        raise PermissionLeaseError("ERROR_PERMISSION_LEASE_DUPLICATE_SCOPE", "tool scope contains duplicates")
    if scope_type == "RUN_READ_ONLY":
        if tool_names or tool_prefixes or allow_mutating:
            raise PermissionLeaseError(
                "ERROR_PERMISSION_LEASE_SCOPE",
                "RUN_READ_ONLY cannot name tools/prefixes or allow mutation",
            )
    elif scope_type == "EXACT_TOOLS":
        if not tool_names or tool_prefixes:
            raise PermissionLeaseError("ERROR_PERMISSION_LEASE_SCOPE", "EXACT_TOOLS requires only tool_names")
        unknown = sorted(name for name in tool_names if name not in TOOL_REGISTRY)
        if unknown:
            raise PermissionLeaseError("ERROR_PERMISSION_LEASE_UNKNOWN_TOOL", ",".join(unknown))
    elif scope_type == "TOOL_PREFIXES":
        if not tool_prefixes or tool_names:
            raise PermissionLeaseError("ERROR_PERMISSION_LEASE_SCOPE", "TOOL_PREFIXES requires only tool_prefixes")
        for prefix in tool_prefixes:
            if not any(name.startswith(prefix) for name in TOOL_REGISTRY):
                raise PermissionLeaseError("ERROR_PERMISSION_LEASE_UNKNOWN_PREFIX", prefix)


def grant_permission_lease(
    *,
    run_id: str,
    scope_type: str,
    granted_by: str,
    tool_names: list[str] | None = None,
    tool_prefixes: list[str] | None = None,
    workspace_root: str | None = None,
    allow_mutating: bool = False,
    allow_network: bool = False,
    ttl_seconds: int | None = 3600,
    max_uses: int = 64,
    cfg: Settings = settings,
) -> dict[str, Any]:
    if not isinstance(run_id, str) or not run_id.strip():
        raise PermissionLeaseError("ERROR_PERMISSION_LEASE_RUN", "run_id is required")
    if not isinstance(granted_by, str) or not granted_by.strip():
        raise PermissionLeaseError("ERROR_PERMISSION_LEASE_GRANTER", "granted_by is required")
    names = sorted(tool_names or [])
    prefixes = sorted(tool_prefixes or [])
    _validate_scope(
        scope_type=scope_type,
        tool_names=names,
        tool_prefixes=prefixes,
        allow_mutating=bool(allow_mutating),
    )
    if not isinstance(max_uses, int) or isinstance(max_uses, bool) or not 1 <= max_uses <= MAX_USES:
        raise PermissionLeaseError("ERROR_PERMISSION_LEASE_MAX_USES", f"max_uses must be 1..{MAX_USES}")
    if ttl_seconds is not None and (
        not isinstance(ttl_seconds, int)
        or isinstance(ttl_seconds, bool)
        or ttl_seconds < 1
        or ttl_seconds > MAX_TTL_SECONDS
    ):
        raise PermissionLeaseError("ERROR_PERMISSION_LEASE_TTL", f"ttl_seconds must be null or 1..{MAX_TTL_SECONDS}")

    cfg.ensure_dirs()
    created_at = utc_now()
    expires_at = (
        (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if ttl_seconds is not None
        else None
    )
    lease_id = stable_id("lease", run_id, scope_type, granted_by.strip(), created_at, canonical_sha256({"names": names, "prefixes": prefixes}), length=20)
    lease = {
        "schema_version": LEASE_SCHEMA_VERSION,
        "lease_id": lease_id,
        "run_id": run_id.strip(),
        "scope_type": scope_type,
        "tool_names": names,
        "tool_prefixes": prefixes,
        "workspace_root": _canonical_workspace(workspace_root),
        "allow_mutating": bool(allow_mutating),
        "allow_network": bool(allow_network),
        "granted_by": granted_by.strip(),
        "created_at": created_at,
        "expires_at": expires_at,
        "max_uses": max_uses,
        "used_call_ids": [],
        "state": "ACTIVE",
        "revoked_at": None,
        "revoked_by": None,
        "grant_sha256": "",
    }
    lease["grant_sha256"] = canonical_sha256(_grant_payload(lease))
    _validate_schema(lease)
    _append_audit(
        {
            "event": "LEASE_GRANTED",
            "lease_id": lease_id,
            "run_id": lease["run_id"],
            "grant_sha256": lease["grant_sha256"],
            "granted_by": lease["granted_by"],
        },
        cfg,
    )

    def append(leases: list[dict[str, Any]]) -> None:
        if any(row.get("lease_id") == lease_id for row in leases if isinstance(row, dict)):
            raise PermissionLeaseError("ERROR_PERMISSION_LEASE_EXISTS", lease_id)
        leases.append(lease)

    update_json(_leases_path(cfg), [], append)
    return {"status": "PERMISSION_LEASE_GRANTED", "lease": lease}


def _effective_state(lease: dict[str, Any]) -> str:
    if lease["state"] == "REVOKED":
        return "REVOKED"
    if len(lease["used_call_ids"]) >= lease["max_uses"]:
        return "EXHAUSTED"
    if lease["expires_at"] is not None and datetime.now(UTC) >= _parse_utc(lease["expires_at"]):
        return "EXPIRED"
    return "ACTIVE"


def list_permission_leases(*, run_id: str | None = None, cfg: Settings = settings) -> dict[str, Any]:
    cfg.ensure_dirs()
    rows = read_json(_leases_path(cfg), [])
    if not isinstance(rows, list):
        raise PermissionLeaseError("ERROR_PERMISSION_LEASE_STORE", "permission lease store is not an array")
    leases: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise PermissionLeaseError("ERROR_PERMISSION_LEASE_STORE", "permission lease row is not an object")
        _validate_integrity(row, cfg)
        if run_id is not None and row["run_id"] != run_id:
            continue
        leases.append({**row, "effective_state": _effective_state(row)})
    return {"status": "PERMISSION_LEASES_OK", "leases": leases}


def revoke_permission_lease(
    lease_id: str,
    *,
    revoked_by: str,
    cfg: Settings = settings,
) -> dict[str, Any]:
    if not isinstance(revoked_by, str) or not revoked_by.strip():
        raise PermissionLeaseError("ERROR_PERMISSION_LEASE_REVOKER", "revoked_by is required")

    def mutate(leases: list[dict[str, Any]]) -> dict[str, Any]:
        matches = [(index, row) for index, row in enumerate(leases) if isinstance(row, dict) and row.get("lease_id") == lease_id]
        if len(matches) != 1:
            raise PermissionLeaseError("ERROR_PERMISSION_LEASE_NOT_FOUND" if not matches else "ERROR_PERMISSION_LEASE_DUPLICATE", lease_id)
        index, lease = matches[0]
        _validate_integrity(lease, cfg)
        if lease["state"] == "REVOKED":
            return lease
        updated = {
            **lease,
            "state": "REVOKED",
            "revoked_at": utc_now(),
            "revoked_by": revoked_by.strip(),
        }
        _validate_schema(updated)
        leases[index] = updated
        return updated

    updated = update_json(_leases_path(cfg), [], mutate)
    _append_audit(
        {
            "event": "LEASE_REVOKED",
            "lease_id": lease_id,
            "run_id": updated["run_id"],
            "grant_sha256": updated["grant_sha256"],
            "revoked_by": revoked_by.strip(),
        },
        cfg,
    )
    return {"status": "PERMISSION_LEASE_REVOKED", "lease": updated}


def _call_by_id(tool_call_id: str, cfg: Settings) -> dict[str, Any]:
    matches = [
        call
        for call in list_tool_calls(cfg)["tool_calls"]
        if call.get("tool_call_id") == tool_call_id
    ]
    if len(matches) != 1:
        raise PermissionLeaseError(
            "ERROR_PERMISSION_LEASE_TOOL_CALL_NOT_FOUND" if not matches else "ERROR_PERMISSION_LEASE_TOOL_CALL_DUPLICATE",
            tool_call_id,
        )
    call = matches[0]
    if call.get("status") != "PENDING_APPROVAL":
        raise PermissionLeaseError("ERROR_PERMISSION_LEASE_TOOL_CALL_STATE", f"{tool_call_id}:{call.get('status')}")
    if arguments_digest(call.get("arguments") or {}) != call.get("arguments_sha256"):
        raise PermissionLeaseError("ERROR_PERMISSION_LEASE_ARGUMENT_DRIFT", tool_call_id)
    return call


def _workspace_matches(lease: dict[str, Any], call: dict[str, Any]) -> bool:
    root = lease.get("workspace_root")
    if root is None:
        return True
    arguments = call.get("arguments") or {}
    candidate = arguments.get("repo_root")
    if not isinstance(candidate, str) or not candidate.strip():
        return False
    try:
        return str(Path(candidate).expanduser().resolve()) == root
    except OSError:
        return False


def _scope_matches(lease: dict[str, Any], call: dict[str, Any]) -> bool:
    tool_name = call.get("tool_name")
    if not isinstance(tool_name, str) or tool_name not in TOOL_REGISTRY:
        return False
    tool = TOOL_REGISTRY[tool_name]
    if call.get("run_id") != lease["run_id"]:
        return False
    if _effective_state(lease) != "ACTIVE":
        return False
    if tool.mutates_state and not lease["allow_mutating"]:
        return False
    if tool.network != "none" and not lease["allow_network"]:
        return False
    if not _workspace_matches(lease, call):
        return False

    scope = lease["scope_type"]
    if scope == "RUN_READ_ONLY":
        return not tool.mutates_state
    if scope == "EXACT_TOOLS":
        return tool_name in lease["tool_names"]
    if scope == "TOOL_PREFIXES":
        return any(tool_name.startswith(prefix) for prefix in lease["tool_prefixes"])
    return False


def matching_permission_leases(
    tool_call_id: str,
    *,
    cfg: Settings = settings,
) -> dict[str, Any]:
    call = _call_by_id(tool_call_id, cfg)
    rows = read_json(_leases_path(cfg), [])
    if not isinstance(rows, list):
        raise PermissionLeaseError("ERROR_PERMISSION_LEASE_STORE", "permission lease store is not an array")
    matches: list[dict[str, Any]] = []
    invalid: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            invalid.append({"lease_id": "<non-object>", "status": "ERROR_PERMISSION_LEASE_STORE"})
            continue
        try:
            _validate_integrity(row, cfg)
        except PermissionLeaseError as exc:
            invalid.append({"lease_id": str(row.get("lease_id") or "<unknown>"), "status": exc.status})
            continue
        if _scope_matches(row, call):
            matches.append(row)
    return {
        "status": "PERMISSION_LEASE_MATCHES_OK",
        "tool_call_id": tool_call_id,
        "matching_lease_ids": [row["lease_id"] for row in matches],
        "invalid_leases": invalid,
        "match_count": len(matches),
    }


def authorize_tool_call_with_lease(
    tool_call_id: str,
    *,
    cfg: Settings = settings,
) -> dict[str, Any]:
    call = _call_by_id(tool_call_id, cfg)
    matches = matching_permission_leases(tool_call_id, cfg=cfg)
    ids = matches["matching_lease_ids"]
    if not ids:
        raise PermissionLeaseError("ERROR_PERMISSION_LEASE_NO_MATCH", tool_call_id)
    if len(ids) > 1:
        raise PermissionLeaseError(
            "ERROR_PERMISSION_LEASE_AMBIGUOUS",
            f"{tool_call_id}:{','.join(ids)}",
        )
    lease_id = ids[0]

    def reserve(leases: list[dict[str, Any]]) -> dict[str, Any]:
        hits = [(index, row) for index, row in enumerate(leases) if isinstance(row, dict) and row.get("lease_id") == lease_id]
        if len(hits) != 1:
            raise PermissionLeaseError("ERROR_PERMISSION_LEASE_NOT_FOUND" if not hits else "ERROR_PERMISSION_LEASE_DUPLICATE", lease_id)
        index, lease = hits[0]
        _validate_integrity(lease, cfg)
        fresh_call = _call_by_id(tool_call_id, cfg)
        if not _scope_matches(lease, fresh_call):
            raise PermissionLeaseError("ERROR_PERMISSION_LEASE_SCOPE_CHANGED", lease_id)
        if tool_call_id in lease["used_call_ids"]:
            raise PermissionLeaseError("ERROR_PERMISSION_LEASE_CALL_ALREADY_USED", tool_call_id)
        if len(lease["used_call_ids"]) >= lease["max_uses"]:
            raise PermissionLeaseError("ERROR_PERMISSION_LEASE_EXHAUSTED", lease_id)
        updated = {**lease, "used_call_ids": [*lease["used_call_ids"], tool_call_id]}
        _validate_schema(updated)
        leases[index] = updated
        return updated

    lease = update_json(_leases_path(cfg), [], reserve)
    _append_audit(
        {
            "event": "LEASE_USE_RESERVED",
            "lease_id": lease_id,
            "run_id": lease["run_id"],
            "tool_call_id": tool_call_id,
            "arguments_sha256": call["arguments_sha256"],
            "grant_sha256": lease["grant_sha256"],
        },
        cfg,
    )
    try:
        approved = approve_tool_call(
            tool_call_id,
            cfg=cfg,
            approved_by=f"lease:{lease_id}",
            reason=f"run-scoped permission lease {lease_id}",
            expected_arguments_sha256=call["arguments_sha256"],
        )
    except ToolRuntimeError as exc:
        _append_audit(
            {
                "event": "LEASE_APPROVAL_FAILED",
                "lease_id": lease_id,
                "run_id": lease["run_id"],
                "tool_call_id": tool_call_id,
                "status": exc.status,
                "grant_sha256": lease["grant_sha256"],
            },
            cfg,
        )
        raise PermissionLeaseError(exc.status, exc.detail) from exc

    _append_audit(
        {
            "event": "LEASE_USED",
            "lease_id": lease_id,
            "run_id": lease["run_id"],
            "tool_call_id": tool_call_id,
            "arguments_sha256": call["arguments_sha256"],
            "grant_sha256": lease["grant_sha256"],
        },
        cfg,
    )
    return {
        "status": "TOOL_CALL_APPROVED_BY_LEASE",
        "lease_id": lease_id,
        "tool_call": approved["tool_call"],
    }
