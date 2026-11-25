from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from backend.app.video_vector_relay import (
    SCHEMA_REL,
    _flagpole_domains,
    _read_json,
    build_video_vector_relay_report,
    load_denominator,
    load_gap_matrix,
    validate_denominator,
    validate_denominator_payload,
    validate_gap_matrix,
    validate_gap_matrix_payload,
)

REPO = Path(__file__).resolve().parents[2]


def test_committed_denominator_and_gap_matrix_close() -> None:
    denominator = load_denominator(REPO)
    gap = load_gap_matrix(REPO)

    assert validate_denominator(REPO) == []
    assert validate_gap_matrix(REPO) == []
    assert denominator["denominator_id"] == "VIDEO_VECTOR_RELAY_V1"
    assert denominator["source_manifest"]["sha256"] == "0e47d28633b8615b8b1bba657ac4004f8e308488ea03a4e72b761197f6036058"
    assert len(denominator["capabilities"]) == 14
    assert gap["baseline_counts"] == {"IMPLEMENTED": 4, "PARTIAL": 6, "ABSENT": 4, "UNVERIFIED": 0, "TOTAL": 14}
    assert gap["counts"] == {"IMPLEMENTED": 9, "PARTIAL": 4, "ABSENT": 1, "UNVERIFIED": 0, "TOTAL": 14}


def test_report_stays_open_until_every_row_is_implemented_and_pass() -> None:
    report = build_video_vector_relay_report(REPO)

    assert report["status"] == "VIDEO_VECTOR_RELAY_OPEN"
    assert report["certification_state"] == "OPEN"
    assert report["errors"] == []
    assert report["required_capabilities"] == 14
    assert report["certification_counts"]["PASS"] == 0  # implemented is not certified: nothing here ran a live model
    assert len(report["unresolved_capability_ids"]) == 14
    for open_row in ("VRL-002", "VRL-009", "VRL-010", "VRL-012", "VRL-013"):
        assert open_row in report["unresolved_capability_ids"]


def test_no_row_regressed_against_the_baseline() -> None:
    order = {"ABSENT": 0, "UNVERIFIED": 0, "PARTIAL": 1, "IMPLEMENTED": 2}
    for row in load_gap_matrix(REPO)["rows"]:
        assert order[row["implementation_state"]] >= order[row["baseline_state"]], row["id"]


def test_denominator_negative_controls_reject_identity_and_domain_drift() -> None:
    denominator = load_denominator(REPO)
    schema = _read_json(REPO / SCHEMA_REL)
    domains = _flagpole_domains(REPO)

    duplicate = deepcopy(denominator)
    duplicate["capabilities"].append(deepcopy(duplicate["capabilities"][0]))
    errors = validate_denominator_payload(duplicate, schema=schema, allowed_domains=domains)
    assert "DUPLICATE_CAPABILITY_ID:VRL-001" in errors
    assert "DUPLICATE_CAPABILITY_NAME:concurrent_conversation_runs" in errors

    unknown_domain = deepcopy(denominator)
    unknown_domain["capabilities"][0]["flagpole_domains"] = ["V99"]
    assert "UNKNOWN_FLAGPOLE_DOMAIN:VRL-001:V99" in validate_denominator_payload(unknown_domain, schema=schema, allowed_domains=domains)

    optionalized = deepcopy(denominator)
    optionalized["capabilities"][0]["required"] = False
    assert "CAPABILITY_NOT_REQUIRED:VRL-001" in validate_denominator_payload(optionalized, schema=schema, allowed_domains=domains)

    rebound = deepcopy(denominator)
    rebound["source_manifest"]["sha256"] = "not-a-digest"
    assert any(error.startswith("SCHEMA:source_manifest.sha256") for error in validate_denominator_payload(rebound, schema=schema, allowed_domains=domains))

    renamed = deepcopy(denominator)
    renamed["denominator_id"] = "VIDEO_CLI_PARITY_V1"
    assert "DENOMINATOR_ID_MISMATCH" in validate_denominator_payload(renamed, schema=schema, allowed_domains=domains)


def test_gap_negative_controls_reject_row_loss_unknown_state_and_count_drift() -> None:
    denominator = load_denominator(REPO)
    gap = load_gap_matrix(REPO)

    missing = deepcopy(gap)
    missing["rows"] = missing["rows"][1:]
    errors = validate_gap_matrix_payload(missing, denominator=denominator, root=REPO)
    assert "GAP_ROW_MISSING:VRL-001" in errors
    assert "COUNT_MISMATCH" in errors and "BASELINE_COUNT_MISMATCH" in errors

    unknown = deepcopy(gap)
    unknown["rows"][0]["implementation_state"] = "MAYBE"
    errors = validate_gap_matrix_payload(unknown, denominator=denominator, root=REPO)
    assert "UNKNOWN_IMPLEMENTATION_STATE:VRL-001:MAYBE" in errors
    assert "COUNT_MISMATCH" in errors

    promoted = deepcopy(gap)
    absent = next(row for row in promoted["rows"] if row["id"] == "VRL-009")
    absent["implementation_state"] = "IMPLEMENTED"
    absent["evidence"] = []
    promoted["counts"]["IMPLEMENTED"] += 1
    promoted["counts"]["ABSENT"] -= 1
    assert "IMPLEMENTED_WITHOUT_EVIDENCE:VRL-009" in validate_gap_matrix_payload(promoted, denominator=denominator, root=REPO)

    rewritten_baseline = deepcopy(gap)
    rewritten_baseline["rows"][1]["baseline_state"] = "IMPLEMENTED"
    assert "BASELINE_COUNT_MISMATCH" in validate_gap_matrix_payload(rewritten_baseline, denominator=denominator, root=REPO)


def test_evidence_must_exist_in_the_tree() -> None:
    denominator = load_denominator(REPO)
    gap = load_gap_matrix(REPO)

    phantom = deepcopy(gap)
    phantom["rows"][0]["evidence"].append("backend/app/does_not_exist.py")
    assert "EVIDENCE_PATH_MISSING:VRL-001:backend/app/does_not_exist.py" in validate_gap_matrix_payload(phantom, denominator=denominator, root=REPO)

    escaping = deepcopy(gap)
    escaping["rows"][0]["evidence"] = ["../outside.py", "/etc/hosts"]
    errors = validate_gap_matrix_payload(escaping, denominator=denominator, root=REPO)
    assert "EVIDENCE_PATH_MISSING:VRL-001:../outside.py" in errors
    assert "EVIDENCE_PATH_MISSING:VRL-001:/etc/hosts" in errors


def test_the_denominator_holds_no_content_from_the_recording() -> None:
    # The recording shows another product's conversation list and live investigation text; the frozen
    # artifacts describe behavior only. Each observed_behavior is a sentence about the interface.
    text = (REPO / "_architecture" / "video_vector_relay_v1.json").read_text(encoding="utf-8")
    denominator = load_denominator(REPO)
    assert "no conversation titles or investigation content" in denominator["scope"]
    assert all(len(row["observed_behavior"]) < 320 for row in denominator["capabilities"])
    assert "http" not in text
