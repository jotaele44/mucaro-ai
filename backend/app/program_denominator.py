from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DENOMINATOR_SCHEMA_VERSION = "MUCARO_V1_DENOMINATOR/1.0"
DENOMINATOR_ROW_COUNT = 100
DENOMINATOR_BASELINE_SOURCE_HEAD = "9a06357afa9e34367776b1f46223a803ab23c958"
DENOMINATOR_BASELINE_RAW_PERCENT = 61.5
DENOMINATOR_BASELINE_WEIGHTED_PERCENT = 62.6
DENOMINATOR_SCORING = {"PASS": 1.0, "PROVISIONAL": 0.5, "OPEN": 0.0, "BLOCKED": 0.0, "UNVERIFIED": 0.0}
ALLOWED_STATES = set(DENOMINATOR_SCORING)
ALLOWED_TRACKS = {
    "BACKEND",
    "CLI",
    "PROVENANCE",
    "RETRIEVAL",
    "GIS",
    "OCR",
    "BROWSING",
    "CODING",
    "PACKAGING",
    "GOVERNANCE",
}
EXPECTED_PREFIXES = {
    "BACKEND": "BC-",
    "CLI": "CLI-",
    "PROVENANCE": "PR-",
    "RETRIEVAL": "RT-",
    "GIS": "GIS-",
    "OCR": "OCR-",
    "BROWSING": "WEB-",
    "CODING": "CODE-",
    "PACKAGING": "PKG-",
    "GOVERNANCE": "GOV-",
}
EXPECTED_IDS = frozenset(
    f"{prefix}{index:02d}"
    for prefix in EXPECTED_PREFIXES.values()
    for index in range(1, 11)
)


@dataclass(frozen=True)
class DenominatorValidation:
    status: str
    errors: tuple[str, ...]
    row_count: int
    unique_id_count: int
    track_counts: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "errors": list(self.errors),
            "row_count": self.row_count,
            "unique_id_count": self.unique_id_count,
            "track_counts": dict(self.track_counts),
        }


def denominator_path(project_root: Path) -> Path:
    return project_root / "_architecture" / "mucaro_v1_denominator.json"


def load_denominator(project_root: Path) -> dict[str, Any]:
    payload = json.loads(denominator_path(project_root).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Mucaro V1 denominator must be a JSON object")
    return payload


def validate_denominator(payload: dict[str, Any]) -> DenominatorValidation:
    errors: list[str] = []
    if payload.get("schema_version") != DENOMINATOR_SCHEMA_VERSION:
        errors.append(f"schema_version must equal {DENOMINATOR_SCHEMA_VERSION}")

    baseline = payload.get("baseline")
    if not isinstance(baseline, dict):
        errors.append("baseline must be an object")
        baseline = {}
    if baseline.get("source_head") != DENOMINATOR_BASELINE_SOURCE_HEAD:
        errors.append(f"baseline source_head must equal {DENOMINATOR_BASELINE_SOURCE_HEAD}")
    if baseline.get("raw_completion_percent") != DENOMINATOR_BASELINE_RAW_PERCENT:
        errors.append("baseline raw completion percent drifted from 61.5")
    if baseline.get("weighted_completion_percent") != DENOMINATOR_BASELINE_WEIGHTED_PERCENT:
        errors.append("baseline weighted completion percent drifted from 62.6")
    if baseline.get("scoring") != DENOMINATOR_SCORING:
        errors.append("baseline scoring map drifted from the frozen V1 scoring contract")

    tracks = payload.get("tracks")
    if not isinstance(tracks, dict):
        errors.append("tracks must be an object")
        tracks = {}
    unknown_tracks = sorted(set(tracks) - ALLOWED_TRACKS)
    missing_tracks = sorted(ALLOWED_TRACKS - set(tracks))
    if unknown_tracks:
        errors.append(f"unknown tracks: {','.join(unknown_tracks)}")
    if missing_tracks:
        errors.append(f"missing tracks: {','.join(missing_tracks)}")

    weights = [value.get("weight") for value in tracks.values() if isinstance(value, dict)]
    if len(weights) != len(ALLOWED_TRACKS) or not all(isinstance(weight, int | float) for weight in weights):
        errors.append("every track must declare a numeric weight")
    elif sum(float(weight) for weight in weights) != 100.0:
        errors.append("track weights must sum to 100")

    rows = payload.get("requirements")
    if not isinstance(rows, list):
        errors.append("requirements must be an array")
        rows = []
    row_count = len(rows)
    if row_count != DENOMINATOR_ROW_COUNT:
        errors.append(f"requirements row count must equal {DENOMINATOR_ROW_COUNT}; found {row_count}")

    ids: list[str] = []
    track_counts = {track: 0 for track in sorted(ALLOWED_TRACKS)}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"requirements[{index}] must be an object")
            continue
        requirement_id = row.get("id")
        track = row.get("track")
        state = row.get("state")
        title = row.get("title")
        if not isinstance(requirement_id, str) or not requirement_id:
            errors.append(f"requirements[{index}].id must be non-empty")
        else:
            ids.append(requirement_id)
        if track not in ALLOWED_TRACKS:
            errors.append(f"requirements[{index}].track invalid: {track}")
        else:
            track_counts[str(track)] += 1
            expected_prefix = EXPECTED_PREFIXES[str(track)]
            if isinstance(requirement_id, str) and not requirement_id.startswith(expected_prefix):
                errors.append(f"requirements[{index}] id {requirement_id} does not match track prefix {expected_prefix}")
        if state not in ALLOWED_STATES:
            errors.append(f"requirements[{index}].state invalid: {state}")
        if not isinstance(title, str) or not title.strip():
            errors.append(f"requirements[{index}].title must be non-empty")

    unique_id_count = len(set(ids))
    if unique_id_count != len(ids):
        errors.append("requirement IDs must be globally unique")
    actual_ids = set(ids)
    if actual_ids != EXPECTED_IDS:
        missing_ids = sorted(EXPECTED_IDS - actual_ids)
        unexpected_ids = sorted(actual_ids - EXPECTED_IDS)
        errors.append(
            "requirement ID set must equal the frozen V1 set; "
            f"missing={','.join(missing_ids) or 'none'} unexpected={','.join(unexpected_ids) or 'none'}"
        )
    for track, count in sorted(track_counts.items()):
        if count != 10:
            errors.append(f"track {track} must contain exactly 10 requirements; found {count}")

    return DenominatorValidation(
        status="MUCARO_V1_DENOMINATOR_OK" if not errors else "MUCARO_V1_DENOMINATOR_FAILED",
        errors=tuple(errors),
        row_count=row_count,
        unique_id_count=unique_id_count,
        track_counts=track_counts,
    )


def audit_denominator(project_root: Path) -> dict[str, Any]:
    return validate_denominator(load_denominator(project_root)).as_dict()
