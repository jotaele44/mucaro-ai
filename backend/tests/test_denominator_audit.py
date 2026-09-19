import copy
from pathlib import Path

from backend.app.denominator_audit import apply_denominator_events, audit_current_denominator_state, load_denominator_events
from backend.app.program_denominator import load_denominator

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_current_denominator_overlay_applies_source_backed_events() -> None:
    report = audit_current_denominator_state(PROJECT_ROOT)
    assert report["status"] == "MUCARO_V1_DENOMINATOR_AUDIT_OK"
    assert report["historical_baseline"] == {"raw_completion_percent": 61.5, "weighted_completion_percent": 62.6}
    assert report["current_overlay"]["raw_completion_percent"] == 70.5
    assert report["current_overlay"]["weighted_completion_percent"] == 72.3
    assert report["current_overlay"]["per_track"]["CODING"]["score_percent"] == 55.0
    assert report["current_overlay"]["per_track"]["CODING"]["state_counts"] == {"PASS": 1, "PROVISIONAL": 9}
    assert report["current_overlay"]["per_track"]["GIS"]["score_percent"] == 75.0
    assert report["current_overlay"]["per_track"]["GIS"]["state_counts"] == {"PASS": 5, "PROVISIONAL": 5}
    assert report["current_overlay"]["per_track"]["OCR"]["score_percent"] == 45.0
    assert report["current_overlay"]["per_track"]["OCR"]["state_counts"] == {"OPEN": 3, "PASS": 2, "PROVISIONAL": 5}
    assert report["current_overlay"]["per_track"]["BROWSING"]["score_percent"] == 60.0
    assert report["current_overlay"]["per_track"]["BROWSING"]["state_counts"] == {"OPEN": 2, "PASS": 4, "PROVISIONAL": 4}
    assert len(report["applied_events"]) == 16
    applied = {event["event_id"] for event in report["applied_events"]}
    assert {
        "evt-code03-repo-read-provisional",
        "evt-code04-dirty-state-provisional",
        "evt-code05-patch-receipts-provisional",
        "evt-code06-mutation-runtime-provisional",
        "evt-code07-check-execution-provisional",
        "evt-code08-review-evidence-provisional",
        "evt-code09-git-lifecycle-provisional",
        "evt-code10-regression-corpus-provisional",
        "evt-ocr07-pdf-lineage-provisional",
        "evt-gis09-topology-vector-raster-provisional",
        "evt-gis10-crs-export-certification-provisional",
        "evt-web10-bounded-traversal-provisional",
    }.issubset(applied)


def test_event_ledger_does_not_mutate_frozen_denominator_file() -> None:
    denominator = load_denominator(PROJECT_ROOT)
    before = copy.deepcopy(denominator)
    report = apply_denominator_events(denominator, load_denominator_events(PROJECT_ROOT))
    assert report["status"] == "MUCARO_V1_DENOMINATOR_AUDIT_OK"
    assert denominator == before


def test_event_rejects_from_state_mismatch() -> None:
    denominator = load_denominator(PROJECT_ROOT)
    ledger = load_denominator_events(PROJECT_ROOT)
    ledger["events"][0]["from_state"] = "PASS"
    report = apply_denominator_events(denominator, ledger)
    assert report["status"] == "MUCARO_V1_DENOMINATOR_AUDIT_FAILED"
    assert any("from_state mismatch" in failure for failure in report["failures"])


def test_event_rejects_unknown_requirement() -> None:
    denominator = load_denominator(PROJECT_ROOT)
    ledger = load_denominator_events(PROJECT_ROOT)
    ledger["events"][0]["requirement_id"] = "BC-99"
    report = apply_denominator_events(denominator, ledger)
    assert report["status"] == "MUCARO_V1_DENOMINATOR_AUDIT_FAILED"
    assert any("unknown requirement" in failure for failure in report["failures"])


def test_event_rejects_duplicate_event_ids() -> None:
    denominator = load_denominator(PROJECT_ROOT)
    ledger = load_denominator_events(PROJECT_ROOT)
    ledger["events"][1]["event_id"] = ledger["events"][0]["event_id"]
    report = apply_denominator_events(denominator, ledger)
    assert report["status"] == "MUCARO_V1_DENOMINATOR_AUDIT_FAILED"
    assert any("duplicate event_id" in failure for failure in report["failures"])


def test_event_requires_evidence_and_reason() -> None:
    denominator = load_denominator(PROJECT_ROOT)
    ledger = load_denominator_events(PROJECT_ROOT)
    ledger["events"][0]["evidence"] = []
    report = apply_denominator_events(denominator, ledger)
    assert report["status"] == "MUCARO_V1_DENOMINATOR_AUDIT_FAILED"
    assert any("requires non-empty evidence" in failure for failure in report["failures"])
