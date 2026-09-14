from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

from .program_denominator import (
    ALLOWED_STATES,
    DENOMINATOR_BASELINE_RAW_PERCENT,
    DENOMINATOR_BASELINE_WEIGHTED_PERCENT,
    DENOMINATOR_SCHEMA_VERSION,
    DENOMINATOR_SCORING,
    load_denominator,
    validate_denominator,
)

EVENT_SCHEMA_VERSION = "MUCARO_V1_DENOMINATOR_EVENTS/1.0"
EVENT_LEDGER_PATH = Path("_architecture/mucaro_v1_denominator_events.json")


def load_denominator_events(project_root: Path) -> dict[str, Any]:
    payload = json.loads((project_root / EVENT_LEDGER_PATH).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("denominator event ledger must be a JSON object")
    return payload


def _track_weight_map(denominator: dict[str, Any]) -> dict[str, float]:
    tracks = denominator.get("tracks")
    if not isinstance(tracks, dict):
        return {}
    result: dict[str, float] = {}
    for track, metadata in tracks.items():
        if isinstance(metadata, dict) and isinstance(metadata.get("weight"), int | float):
            result[str(track)] = float(metadata["weight"])
    return result


def apply_denominator_events(denominator: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    baseline_validation = validate_denominator(denominator)
    failures: list[str] = list(baseline_validation.errors)
    if ledger.get("schema_version") != EVENT_SCHEMA_VERSION:
        failures.append("event schema_version mismatch")
    if ledger.get("denominator_version") != DENOMINATOR_SCHEMA_VERSION:
        failures.append("event denominator_version mismatch")

    rows = denominator.get("requirements") if isinstance(denominator.get("requirements"), list) else []
    current_rows = {str(row.get("id")): deepcopy(row) for row in rows if isinstance(row, dict) and row.get("id")}
    events = ledger.get("events")
    if not isinstance(events, list):
        events = []
        failures.append("events must be an array")

    seen_event_ids: set[str] = set()
    applied: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            failures.append(f"events[{index}] must be an object")
            continue
        event_id = event.get("event_id")
        requirement_id = event.get("requirement_id")
        from_state = event.get("from_state")
        to_state = event.get("to_state")
        evidence = event.get("evidence")
        reason = event.get("reason")
        if not isinstance(event_id, str) or not event_id:
            failures.append(f"events[{index}].event_id invalid")
            continue
        if event_id in seen_event_ids:
            failures.append(f"duplicate event_id: {event_id}")
            continue
        seen_event_ids.add(event_id)
        if requirement_id not in current_rows:
            failures.append(f"event {event_id} references unknown requirement {requirement_id}")
            continue
        if from_state not in ALLOWED_STATES or to_state not in ALLOWED_STATES:
            failures.append(f"event {event_id} has invalid state")
            continue
        if not isinstance(evidence, list) or not evidence or not all(isinstance(item, str) and item for item in evidence):
            failures.append(f"event {event_id} requires non-empty evidence")
            continue
        if not isinstance(reason, str) or not reason.strip():
            failures.append(f"event {event_id} requires reason")
            continue
        actual_from = current_rows[requirement_id].get("state")
        if actual_from != from_state:
            failures.append(f"event {event_id} from_state mismatch: expected {actual_from}, got {from_state}")
            continue
        current_rows[requirement_id]["state"] = to_state
        applied.append({"event_id": event_id, "requirement_id": requirement_id, "from_state": from_state, "to_state": to_state})

    ordered_rows = [current_rows[str(row.get("id"))] for row in rows if isinstance(row, dict) and str(row.get("id")) in current_rows]
    state_counts = Counter(str(row.get("state")) for row in ordered_rows)
    raw_score = sum(float(DENOMINATOR_SCORING.get(str(row.get("state")), 0.0)) for row in ordered_rows)
    raw_percent = round(raw_score / len(ordered_rows) * 100.0, 1) if ordered_rows else 0.0

    weights = _track_weight_map(denominator)
    weighted_percent = 0.0
    per_track: dict[str, dict[str, Any]] = {}
    for track, weight in sorted(weights.items()):
        track_rows = [row for row in ordered_rows if row.get("track") == track]
        track_score = sum(float(DENOMINATOR_SCORING.get(str(row.get("state")), 0.0)) for row in track_rows)
        track_percent = round(track_score / len(track_rows) * 100.0, 1) if track_rows else 0.0
        weighted_percent += (track_percent / 100.0) * weight
        per_track[track] = {"weight": weight, "score_percent": track_percent, "state_counts": dict(sorted(Counter(str(row.get("state")) for row in track_rows).items()))}
    weighted_percent = round(weighted_percent, 1)

    return {
        "status": "MUCARO_V1_DENOMINATOR_AUDIT_OK" if not failures else "MUCARO_V1_DENOMINATOR_AUDIT_FAILED",
        "denominator_version": ledger.get("denominator_version"),
        "audit_head": ledger.get("audit_head"),
        "historical_baseline": {
            "raw_completion_percent": DENOMINATOR_BASELINE_RAW_PERCENT,
            "weighted_completion_percent": DENOMINATOR_BASELINE_WEIGHTED_PERCENT,
        },
        "current_overlay": {
            "raw_completion_percent": raw_percent,
            "weighted_completion_percent": weighted_percent,
            "state_counts": dict(sorted(state_counts.items())),
            "per_track": per_track,
        },
        "applied_events": applied,
        "failures": failures,
        "requirements": ordered_rows,
    }


def audit_current_denominator_state(project_root: Path) -> dict[str, Any]:
    return apply_denominator_events(load_denominator(project_root), load_denominator_events(project_root))
