from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import Settings, settings
from .contracts import validate_contract
from .db import ensure_db, get_connection, list_payloads
from .utils import canonical_sha256, read_json, stable_id, update_json, utc_now

FORENSIC_SCHEMA_VERSION = "mucaro-forensic-event/1.0"
FORENSIC_NATIVE_EVENTS_NAME = "forensic_events.json"
TOOL_AUDIT_NAME = "tool_audit_log.json"
DIRECTORY_PERMISSIONS_NAME = "directory_permissions.json"
EVENT_CLASSES = (
    "workflow_transition",
    "tool_call",
    "error",
    "artifact_creation",
    "permission_change",
    "model_routing",
    "source_acquisition",
)
_NATIVE_EVENT_CLASSES = {"permission_change"}
_EPOCH = "1970-01-01T00:00:00Z"


class ForensicTrailError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _native_path(cfg: Settings) -> Path:
    return cfg.processed_dir / FORENSIC_NATIVE_EVENTS_NAME


def _event(
    *,
    event_class: str,
    event_type: str,
    created_at: str,
    origin: str,
    origin_id: str | None,
    data: dict[str, Any],
    previous_event_sha256: str | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    if event_class not in EVENT_CLASSES:
        raise ForensicTrailError("ERROR_FORENSIC_EVENT_CLASS", event_class)
    body: dict[str, Any] = {
        "schema_version": FORENSIC_SCHEMA_VERSION,
        "event_id": event_id
        or stable_id("forevent", event_class, event_type, origin, str(origin_id), created_at, length=16),
        "event_class": event_class,
        "event_type": event_type,
        "created_at": created_at or _EPOCH,
        "origin": origin,
        "origin_id": origin_id,
        "data": data,
        "previous_event_sha256": previous_event_sha256,
    }
    body["event_sha256"] = canonical_sha256(body)
    return validate_contract("forensic_event", body)


def append_forensic_event(
    event_class: str,
    event_type: str,
    *,
    origin: str,
    origin_id: str | None,
    data: dict[str, Any],
    cfg: Settings = settings,
) -> dict[str, Any]:
    """Append history only for classes whose canonical state stores do not retain changes."""
    if event_class not in _NATIVE_EVENT_CLASSES:
        raise ForensicTrailError(
            "ERROR_FORENSIC_NATIVE_CLASS",
            f"{event_class} is derived from an existing authoritative event stream and must not be duplicated here.",
        )
    cfg.ensure_dirs()

    def append(rows: list[Any]) -> dict[str, Any]:
        valid = [row for row in rows if isinstance(row, dict)]
        previous = valid[-1].get("event_sha256") if valid else None
        event = _event(
            event_class=event_class,
            event_type=event_type,
            created_at=utc_now(),
            origin=origin,
            origin_id=origin_id,
            data=data,
            previous_event_sha256=str(previous) if previous else None,
        )
        rows.append(event)
        return event

    return update_json(_native_path(cfg), [], append)


def load_native_forensic_events(cfg: Settings = settings) -> list[dict[str, Any]]:
    rows = read_json(_native_path(cfg), [])
    return [row for row in rows if isinstance(row, dict)]


def verify_native_forensic_chain(cfg: Settings = settings) -> dict[str, Any]:
    events = load_native_forensic_events(cfg)
    previous: str | None = None
    for index, event in enumerate(events):
        try:
            validate_contract("forensic_event", event)
        except Exception as exc:  # fail closed on any schema-level corruption
            return {
                "status": "FORENSIC_NATIVE_CHAIN_BROKEN",
                "event_count": len(events),
                "index": index,
                "reason": f"CONTRACT:{type(exc).__name__}",
            }
        observed = event.get("event_sha256")
        body = {key: value for key, value in event.items() if key != "event_sha256"}
        if canonical_sha256(body) != observed:
            return {
                "status": "FORENSIC_NATIVE_CHAIN_BROKEN",
                "event_count": len(events),
                "index": index,
                "reason": "EVENT_HASH_MISMATCH",
            }
        if event.get("previous_event_sha256") != previous:
            return {
                "status": "FORENSIC_NATIVE_CHAIN_BROKEN",
                "event_count": len(events),
                "index": index,
                "reason": "PREVIOUS_HASH_MISMATCH",
            }
        previous = str(observed)
    return {
        "status": "FORENSIC_NATIVE_CHAIN_OK",
        "event_count": len(events),
        "head_sha256": previous,
    }


def _safe_run_data(event_type: str, data: Any) -> dict[str, Any]:
    source = data if isinstance(data, dict) else {}
    if event_type == "route":
        keys = ("status", "selected_provider", "selected_model", "selection_reason", "route_sha256")
    elif event_type == "artifact":
        keys = ("artifact_id", "artifact_type", "logical_identity")
    elif event_type in {"warning", "error", "interrupted", "cancelled"}:
        keys = ("status", "category", "outcome")
    elif event_type in {"tool_call", "approval_request", "tool_started", "tool_result", "tool_summary"}:
        keys = ("tool_call_id", "tool_name", "status", "result_status", "round")
    elif event_type in {"retrieval", "citation"}:
        keys = ("strategy", "chunk_id", "source_id", "retrieval_rank")
    else:
        keys = ("status", "state", "outcome", "mode", "round")
    return {key: source.get(key) for key in keys if source.get(key) is not None}


def _run_event_class(event_type: str) -> str:
    if event_type == "route":
        return "model_routing"
    if event_type == "artifact":
        return "artifact_creation"
    if event_type in {"warning", "error", "interrupted", "cancelled"}:
        return "error"
    if event_type in {"tool_call", "approval_request", "tool_started", "tool_result", "tool_summary"}:
        return "tool_call"
    return "workflow_transition"


def _derived_run_events(cfg: Settings) -> list[dict[str, Any]]:
    ensure_db(cfg.sqlite_path)
    with get_connection(cfg.sqlite_path) as conn:
        rows = conn.execute("SELECT payload_json FROM run_events ORDER BY rowid ASC").fetchall()
    events: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        event_type = str(payload.get("type") or "unknown")
        run_id = str(payload.get("run_id") or "") or None
        created_at = str(payload.get("created_at") or _EPOCH)
        events.append(
            _event(
                event_class=_run_event_class(event_type),
                event_type=f"RUN_{event_type.upper()}",
                created_at=created_at,
                origin="work_run_event",
                origin_id=f"{run_id}:{payload.get('seq')}",
                data={"run_id": run_id, "seq": payload.get("seq"), **_safe_run_data(event_type, payload.get("data"))},
            )
        )
    return events


def _derived_tool_events(cfg: Settings) -> list[dict[str, Any]]:
    rows = read_json(cfg.processed_dir / TOOL_AUDIT_NAME, [])
    events: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        tool_event = str(row.get("event") or "UNKNOWN")
        event_class = "error" if any(token in tool_event for token in ("FAILED", "REJECTED", "INTERRUPTED")) else "tool_call"
        events.append(
            _event(
                event_class=event_class,
                event_type=f"TOOL_{tool_event}",
                created_at=str(row.get("created_at") or _EPOCH),
                origin="tool_audit",
                origin_id=str(row.get("event_id") or row.get("tool_call_id") or "") or None,
                data={
                    key: row.get(key)
                    for key in ("tool_call_id", "tool_name", "event", "actor", "status")
                    if row.get(key) is not None
                },
            )
        )
    return events


def _derived_answer_trace_events(cfg: Settings) -> list[dict[str, Any]]:
    ensure_db(cfg.sqlite_path)
    events: list[dict[str, Any]] = []
    for trace in list_payloads("runs", cfg.sqlite_path):
        if not isinstance(trace, dict):
            continue
        run_id = str(trace.get("run_id") or "") or None
        created_at = str(trace.get("created_at") or _EPOCH)
        if trace.get("model_provider") or trace.get("model_route_sha256"):
            events.append(
                _event(
                    event_class="model_routing",
                    event_type="ANSWER_MODEL_ROUTE",
                    created_at=created_at,
                    origin="answer_trace",
                    origin_id=run_id,
                    data={
                        key: trace.get(key)
                        for key in (
                            "run_id",
                            "model_provider",
                            "model_name",
                            "model_route_sha256",
                            "model_route_selection_reason",
                            "model_route_fallback_reason",
                        )
                        if trace.get(key) is not None
                    },
                )
            )
        exports = trace.get("export_paths")
        if isinstance(exports, dict) and exports:
            events.append(
                _event(
                    event_class="artifact_creation",
                    event_type="ANSWER_EXPORT_SET",
                    created_at=created_at,
                    origin="answer_trace",
                    origin_id=run_id,
                    data={"run_id": run_id, "artifact_kinds": sorted(str(key) for key in exports)},
                )
            )
    return events


def _derived_source_state_events(cfg: Settings) -> list[dict[str, Any]]:
    ensure_db(cfg.sqlite_path)
    events: list[dict[str, Any]] = []
    for source in list_payloads("sources", cfg.sqlite_path):
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("source_id") or "") or None
        provenance = source.get("provenance") if isinstance(source.get("provenance"), dict) else {}
        events.append(
            _event(
                event_class="source_acquisition",
                event_type="SOURCE_STATE_MANIFESTATION",
                created_at=str(source.get("ingested_at") or provenance.get("retrieval_utc") or _EPOCH),
                origin="source_manifest",
                origin_id=source_id,
                data={
                    "source_id": source_id,
                    "source_sha256": source.get("source_sha256"),
                    "bytes": source.get("bytes"),
                    "parser": source.get("parser"),
                    "lineage_status": source.get("lineage_status"),
                    "source_kind": provenance.get("source_kind"),
                },
            )
        )
    return events


def _derived_permission_state_events(cfg: Settings) -> list[dict[str, Any]]:
    rows = read_json(cfg.processed_dir / DIRECTORY_PERMISSIONS_NAME, [])
    events: list[dict[str, Any]] = []
    for grant in rows if isinstance(rows, list) else []:
        if not isinstance(grant, dict):
            continue
        root = str(grant.get("canonical_root") or "")
        root_sha256 = canonical_sha256(root)
        events.append(
            _event(
                event_class="permission_change",
                event_type="PERMISSION_STATE_MANIFESTATION",
                created_at=str(grant.get("granted_at") or _EPOCH),
                origin="directory_permission_ledger",
                origin_id=root_sha256,
                data={
                    "canonical_root_sha256": root_sha256,
                    "status": grant.get("status"),
                    "granted_by": grant.get("granted_by"),
                },
            )
        )
    return events


def build_forensic_trail(cfg: Settings = settings, *, limit: int = 500) -> dict[str, Any]:
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 5000:
        raise ForensicTrailError("ERROR_FORENSIC_LIMIT", "limit must be between 1 and 5000")

    native_chain = verify_native_forensic_chain(cfg)
    events = (
        load_native_forensic_events(cfg)
        + _derived_run_events(cfg)
        + _derived_tool_events(cfg)
        + _derived_answer_trace_events(cfg)
        + _derived_source_state_events(cfg)
        + _derived_permission_state_events(cfg)
    )
    events = sorted(
        events,
        key=lambda row: (
            str(row.get("created_at") or _EPOCH),
            str(row.get("event_class") or ""),
            str(row.get("event_id") or ""),
        ),
    )
    counts = {event_class: 0 for event_class in EVENT_CLASSES}
    for event in events:
        event_class = str(event.get("event_class") or "")
        if event_class in counts:
            counts[event_class] += 1
    returned = events[-limit:]
    status = "FORENSIC_TRAIL_OK" if native_chain["status"] == "FORENSIC_NATIVE_CHAIN_OK" else "FORENSIC_TRAIL_FAIL"
    return {
        "status": status,
        "schema_version": "mucaro-forensic-trail/1.0",
        "supported_event_classes": list(EVENT_CLASSES),
        "event_class_counts": counts,
        "total_event_count": len(events),
        "returned_event_count": len(returned),
        "truncated_count": len(events) - len(returned),
        "events": returned,
        "trail_sha256": canonical_sha256(events),
        "native_event_chain": native_chain,
        "invariants": {
            "event_count_closes": len(events) == sum(counts.values()),
            "returned_count_closes": len(returned) + (len(events) - len(returned)) == len(events),
            "native_chain_valid": native_chain["status"] == "FORENSIC_NATIVE_CHAIN_OK",
            "private_payload_fields_omitted": True,
        },
        "limitations": [
            "Event counts are source manifestations, not canonical action identities; one action may appear in more than one authoritative stream.",
            "Permission-change history begins when this forensic event ledger is deployed; current pre-existing grants are represented separately as state manifestations.",\n            "Source acquisition is derived from canonical retained source manifests and their ingestion timestamps; repeated ingestion of the same stable source identity is not reconstructed as a distinct historical event.",
            "The composite trail is a deterministic read model. Canonical run, tool, source, permission and answer-trace stores remain authoritative for their own record types.",
            "Run-event rows are not independently hash-chained; only native forensic history and the existing tool-audit stream have chain semantics.",
        ],
    }
