from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from backend.app.video_cli_parity import (
    build_video_cli_parity_report,
    load_denominator,
    load_gap_matrix,
    validate_denominator,
    validate_denominator_payload,
    validate_gap_matrix,
    validate_gap_matrix_payload,
)
from backend.app.video_cli_parity import _flagpole_domains, _read_json, SCHEMA_REL

REPO = Path(__file__).resolve().parents[2]


def test_committed_video_cli_parity_denominator_and_gap_matrix_close() -> None:
    denominator = load_denominator(REPO)
    gap = load_gap_matrix(REPO)

    assert validate_denominator(REPO) == []
    assert validate_gap_matrix(REPO) == []
    assert denominator["denominator_id"] == "VIDEO_CLI_PARITY_V1"
    assert len(denominator["capabilities"]) == 27
    assert gap["counts"] == {
        "IMPLEMENTED": 8,
        "PARTIAL": 17,
        "ABSENT": 2,
        "UNVERIFIED": 0,
        "TOTAL": 27,
    }


def test_report_fails_closed_until_every_required_row_is_implemented_and_pass() -> None:
    report = build_video_cli_parity_report(REPO)

    assert report["status"] == "VIDEO_CLI_PARITY_OPEN"
    assert report["certification_state"] == "OPEN"
    assert report["required_capabilities"] == 27
    assert report["errors"] == []
    assert report["implementation_counts"] == {
        "IMPLEMENTED": 8,
        "PARTIAL": 17,
        "ABSENT": 2,
        "UNVERIFIED": 0,
    }
    assert "VCP-010" in report["unresolved_capability_ids"]
    assert "VCP-027" in report["unresolved_capability_ids"]


def test_denominator_negative_controls_reject_identity_and_domain_drift() -> None:
    denominator = load_denominator(REPO)
    schema = _read_json(REPO / SCHEMA_REL)
    domains = _flagpole_domains(REPO)

    duplicate = deepcopy(denominator)
    duplicate["capabilities"].append(deepcopy(duplicate["capabilities"][0]))
    errors = validate_denominator_payload(duplicate, schema=schema, allowed_domains=domains)
    assert "DUPLICATE_CAPABILITY_ID:VCP-001" in errors
    assert "DUPLICATE_CAPABILITY_NAME:persistent_terminal_agent_session" in errors

    unknown_domain = deepcopy(denominator)
    unknown_domain["capabilities"][0]["flagpole_domains"] = ["V99"]
    errors = validate_denominator_payload(unknown_domain, schema=schema, allowed_domains=domains)
    assert "UNKNOWN_FLAGPOLE_DOMAIN:VCP-001:V99" in errors

    optionalized = deepcopy(denominator)
    optionalized["capabilities"][0]["required"] = False
    errors = validate_denominator_payload(optionalized, schema=schema, allowed_domains=domains)
    assert "CAPABILITY_NOT_REQUIRED:VCP-001" in errors


def test_gap_negative_controls_reject_row_loss_unknown_state_and_count_drift() -> None:
    denominator = load_denominator(REPO)
    gap = load_gap_matrix(REPO)

    missing = deepcopy(gap)
    missing["rows"] = missing["rows"][1:]
    errors = validate_gap_matrix_payload(missing, denominator=denominator)
    assert "GAP_ROW_MISSING:VCP-001" in errors
    assert "COUNT_MISMATCH" in errors

    unknown = deepcopy(gap)
    unknown["rows"][0]["implementation_state"] = "MAYBE"
    errors = validate_gap_matrix_payload(unknown, denominator=denominator)
    assert "UNKNOWN_IMPLEMENTATION_STATE:VCP-001:MAYBE" in errors
    assert "COUNT_MISMATCH" in errors

    unsupported_pass = deepcopy(gap)
    unsupported_pass["rows"][0]["implementation_state"] = "IMPLEMENTED"
    unsupported_pass["rows"][0]["evidence"] = []
    unsupported_pass["counts"]["IMPLEMENTED"] += 1
    unsupported_pass["counts"]["PARTIAL"] -= 1
    errors = validate_gap_matrix_payload(unsupported_pass, denominator=denominator)
    assert "IMPLEMENTED_WITHOUT_EVIDENCE:VCP-001" in errors
