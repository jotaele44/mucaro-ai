from copy import deepcopy

from backend.app.program_denominator import (
    BASELINE_WEIGHTED_COMPLETION_PERCENT,
    DENOMINATOR_VERSION,
    build_v1_denominator,
    validate_v1_denominator,
)


def test_v1_denominator_validates_and_freezes_baseline():
    payload = build_v1_denominator()
    report = validate_v1_denominator(payload)
    assert report["status"] == "MUCARO_V1_DENOMINATOR_OK"
    assert report["requirement_count"] == 100
    assert report["track_count"] == 10
    assert set(report["track_counts"].values()) == {10}
    assert report["denominator_version"] == DENOMINATOR_VERSION
    assert report["baseline_weighted_completion_percent"] == BASELINE_WEIGHTED_COMPLETION_PERCENT == 62.6


def test_v1_denominator_rejects_duplicate_id():
    payload = build_v1_denominator()
    payload["requirements"][1]["id"] = payload["requirements"][0]["id"]
    report = validate_v1_denominator(payload)
    assert report["status"] == "MUCARO_V1_DENOMINATOR_FAILED"
    assert "requirement_id_uniqueness" in report["failures"]
    assert "requirement_membership" in report["failures"]


def test_v1_denominator_rejects_missing_row():
    payload = build_v1_denominator()
    payload["requirements"].pop()
    report = validate_v1_denominator(payload)
    assert "requirement_count" in report["failures"]
    assert "requirement_membership" in report["failures"]


def test_v1_denominator_rejects_unknown_state():
    payload = build_v1_denominator()
    payload["requirements"][0]["state"] = "DONE"
    report = validate_v1_denominator(payload)
    assert "allowed_states" in report["failures"]


def test_v1_denominator_rejects_silent_membership_mutation():
    payload = build_v1_denominator()
    payload["requirements"][0]["id"] = "BC-99"
    report = validate_v1_denominator(payload)
    assert "requirement_membership" in report["failures"]


def test_v1_denominator_rejects_version_change_without_new_validator_contract():
    payload = build_v1_denominator()
    payload["denominator_version"] = "MUCARO_V1_DENOMINATOR/1.1"
    report = validate_v1_denominator(payload)
    assert "denominator_version" in report["failures"]


def test_v1_denominator_allows_evidence_driven_state_regression_without_membership_change():
    payload = deepcopy(build_v1_denominator())
    payload["requirements"][0]["state"] = "OPEN"
    report = validate_v1_denominator(payload)
    assert report["status"] == "MUCARO_V1_DENOMINATOR_OK"
    assert report["requirement_count"] == 100
