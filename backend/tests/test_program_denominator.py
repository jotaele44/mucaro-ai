from __future__ import annotations

import copy
from pathlib import Path

from backend.app.program_denominator import (
    DENOMINATOR_BASELINE_RAW_PERCENT,
    DENOMINATOR_BASELINE_WEIGHTED_PERCENT,
    DENOMINATOR_ROW_COUNT,
    DENOMINATOR_SCHEMA_VERSION,
    audit_denominator,
    load_denominator,
    validate_denominator,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_frozen_v1_denominator_passes_all_invariants() -> None:
    report = audit_denominator(PROJECT_ROOT)
    assert report["status"] == "MUCARO_V1_DENOMINATOR_OK"
    assert report["errors"] == []
    assert report["row_count"] == DENOMINATOR_ROW_COUNT == 100
    assert report["unique_id_count"] == 100
    assert set(report["track_counts"].values()) == {10}


def test_frozen_v1_denominator_version_and_baseline_are_locked() -> None:
    payload = load_denominator(PROJECT_ROOT)
    assert payload["schema_version"] == DENOMINATOR_SCHEMA_VERSION == "MUCARO_V1_DENOMINATOR/1.0"
    assert payload["baseline"]["raw_completion_percent"] == DENOMINATOR_BASELINE_RAW_PERCENT == 61.5
    assert payload["baseline"]["weighted_completion_percent"] == DENOMINATOR_BASELINE_WEIGHTED_PERCENT == 62.6
    assert sum(track["weight"] for track in payload["tracks"].values()) == 100


def test_denominator_rejects_row_deletion() -> None:
    payload = copy.deepcopy(load_denominator(PROJECT_ROOT))
    payload["requirements"].pop()
    report = validate_denominator(payload)
    assert report.status == "MUCARO_V1_DENOMINATOR_FAILED"
    assert any("row count" in error for error in report.errors)


def test_denominator_rejects_duplicate_ids() -> None:
    payload = copy.deepcopy(load_denominator(PROJECT_ROOT))
    payload["requirements"][1]["id"] = payload["requirements"][0]["id"]
    report = validate_denominator(payload)
    assert report.status == "MUCARO_V1_DENOMINATOR_FAILED"
    assert "requirement IDs must be globally unique" in report.errors


def test_denominator_rejects_version_drift() -> None:
    payload = copy.deepcopy(load_denominator(PROJECT_ROOT))
    payload["schema_version"] = "MUCARO_V1_DENOMINATOR/1.1"
    report = validate_denominator(payload)
    assert report.status == "MUCARO_V1_DENOMINATOR_FAILED"
    assert any("schema_version" in error for error in report.errors)


def test_denominator_rejects_baseline_drift() -> None:
    payload = copy.deepcopy(load_denominator(PROJECT_ROOT))
    payload["baseline"]["weighted_completion_percent"] = 63.0
    report = validate_denominator(payload)
    assert report.status == "MUCARO_V1_DENOMINATOR_FAILED"
    assert "baseline weighted completion percent drifted from 62.6" in report.errors


def test_denominator_rejects_unknown_state_and_track() -> None:
    payload = copy.deepcopy(load_denominator(PROJECT_ROOT))
    payload["requirements"][0]["state"] = "CERTIFIED"
    payload["requirements"][1]["track"] = "NEW_TRACK"
    report = validate_denominator(payload)
    assert report.status == "MUCARO_V1_DENOMINATOR_FAILED"
    assert any("state invalid" in error for error in report.errors)
    assert any("track invalid" in error for error in report.errors)


def test_denominator_rejects_weight_drift() -> None:
    payload = copy.deepcopy(load_denominator(PROJECT_ROOT))
    payload["tracks"]["GIS"]["weight"] = 13
    report = validate_denominator(payload)
    assert report.status == "MUCARO_V1_DENOMINATOR_FAILED"
    assert "track weights must sum to 100" in report.errors
