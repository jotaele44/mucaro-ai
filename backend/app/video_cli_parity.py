from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

DENOMINATOR_REL = Path("_architecture") / "video_cli_parity_v1.json"
GAP_MATRIX_REL = Path("_architecture") / "video_cli_parity_gap_matrix_v1.json"
SCHEMA_REL = Path("schemas") / "video_cli_parity.schema.json"
FLAGPOLE_REL = Path("_architecture") / "mucaro_flagpole_v1.json"

DENOMINATOR_SCHEMA = "mucaro-video-cli-parity-denominator/1.0"
GAP_SCHEMA = "mucaro-video-cli-parity-gap-matrix/1.0"
DENOMINATOR_ID = "VIDEO_CLI_PARITY_V1"
IMPLEMENTATION_STATES = ("IMPLEMENTED", "PARTIAL", "ABSENT", "UNVERIFIED")
CERTIFICATION_STATES = (
    "PASS",
    "FAIL",
    "OPEN",
    "BLOCKED",
    "PROVISIONAL",
    "AUDIT_ONLY",
    "NONCANONICAL",
    "CANDIDATE_NOT_IDENTITY",
    "UNRESOLVED",
    "SUPERSEDED",
)


class VideoCliParityError(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VideoCliParityError(f"unable to read JSON artifact: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise VideoCliParityError(f"JSON artifact must be an object: {path}")
    return value


def load_denominator(root: str | Path) -> dict[str, Any]:
    return _read_json(Path(root) / DENOMINATOR_REL)


def load_gap_matrix(root: str | Path) -> dict[str, Any]:
    return _read_json(Path(root) / GAP_MATRIX_REL)


def _flagpole_domains(root: Path) -> set[str]:
    payload = _read_json(root / FLAGPOLE_REL)
    domains = payload.get("domains")
    if not isinstance(domains, list):
        raise VideoCliParityError("flagpole domains are missing")
    return {
        str(row.get("id"))
        for row in domains
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }


def validate_denominator_payload(
    payload: dict[str, Any],
    *,
    schema: dict[str, Any],
    allowed_domains: set[str],
) -> list[str]:
    errors: list[str] = []
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        return [f"SCHEMA_INVALID:{exc.message}"]

    validator = Draft202012Validator(schema)
    for error in sorted(validator.iter_errors(payload), key=lambda item: list(item.absolute_path)):
        where = ".".join(str(part) for part in error.absolute_path) or "$"
        errors.append(f"SCHEMA:{where}:{error.message}")

    if payload.get("schema") != DENOMINATOR_SCHEMA:
        errors.append("SCHEMA_MISMATCH")
    if payload.get("denominator_id") != DENOMINATOR_ID:
        errors.append("DENOMINATOR_ID_MISMATCH")

    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, list):
        return sorted(set([*errors, "DENOMINATOR_SHAPE"]))

    ids = [row.get("id") for row in capabilities if isinstance(row, dict)]
    names = [row.get("capability") for row in capabilities if isinstance(row, dict)]
    for value, count in Counter(ids).items():
        if value is not None and count > 1:
            errors.append(f"DUPLICATE_CAPABILITY_ID:{value}")
    for value, count in Counter(names).items():
        if value is not None and count > 1:
            errors.append(f"DUPLICATE_CAPABILITY_NAME:{value}")

    for row in capabilities:
        if not isinstance(row, dict):
            errors.append("CAPABILITY_NOT_OBJECT")
            continue
        row_id = row.get("id", "UNKNOWN")
        if row.get("required") is not True:
            errors.append(f"CAPABILITY_NOT_REQUIRED:{row_id}")
        domains = row.get("flagpole_domains")
        if isinstance(domains, list):
            for domain in domains:
                if domain not in allowed_domains:
                    errors.append(f"UNKNOWN_FLAGPOLE_DOMAIN:{row_id}:{domain}")

    return sorted(set(errors))


def validate_denominator(root: str | Path) -> list[str]:
    root_path = Path(root)
    return validate_denominator_payload(
        load_denominator(root_path),
        schema=_read_json(root_path / SCHEMA_REL),
        allowed_domains=_flagpole_domains(root_path),
    )


def validate_gap_matrix_payload(
    gap: dict[str, Any],
    *,
    denominator: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if gap.get("schema") != GAP_SCHEMA:
        errors.append("GAP_SCHEMA_MISMATCH")
    if gap.get("denominator_id") != denominator.get("denominator_id"):
        errors.append("GAP_DENOMINATOR_ID_MISMATCH")
    if gap.get("baseline_main_sha") != denominator.get("baseline_main_sha"):
        errors.append("GAP_BASELINE_SHA_MISMATCH")

    rows = gap.get("rows")
    if not isinstance(rows, list):
        return sorted(set([*errors, "GAP_MATRIX_SHAPE"]))

    denominator_ids = {
        row.get("id")
        for row in denominator.get("capabilities", [])
        if isinstance(row, dict)
    }
    row_ids = [row.get("id") for row in rows if isinstance(row, dict)]
    for value, count in Counter(row_ids).items():
        if value is not None and count > 1:
            errors.append(f"DUPLICATE_GAP_ID:{value}")

    row_id_set = set(row_ids)
    for missing in sorted(denominator_ids - row_id_set):
        errors.append(f"GAP_ROW_MISSING:{missing}")
    for extra in sorted(row_id_set - denominator_ids):
        errors.append(f"GAP_ROW_UNKNOWN:{extra}")

    computed = Counter()
    for row in rows:
        if not isinstance(row, dict):
            errors.append("GAP_ROW_NOT_OBJECT")
            continue
        row_id = row.get("id", "UNKNOWN")
        state = row.get("implementation_state")
        cert = row.get("certification_state")
        if state not in IMPLEMENTATION_STATES:
            errors.append(f"UNKNOWN_IMPLEMENTATION_STATE:{row_id}:{state}")
        else:
            computed[state] += 1
        if cert not in CERTIFICATION_STATES:
            errors.append(f"UNKNOWN_CERTIFICATION_STATE:{row_id}:{cert}")
        evidence = row.get("evidence")
        if not isinstance(evidence, list) or not all(isinstance(item, str) and item for item in evidence):
            errors.append(f"INVALID_EVIDENCE:{row_id}")
        if state == "IMPLEMENTED" and not evidence:
            errors.append(f"IMPLEMENTED_WITHOUT_EVIDENCE:{row_id}")
        if not isinstance(row.get("gap"), str) or not row.get("gap", "").strip():
            errors.append(f"GAP_TEXT_MISSING:{row_id}")

    counts = gap.get("counts")
    expected_counts = {state: computed[state] for state in IMPLEMENTATION_STATES}
    expected_counts["TOTAL"] = len(rows)
    if counts != expected_counts:
        errors.append("COUNT_MISMATCH")

    declared_impl = gap.get("implementation_states")
    declared_cert = gap.get("certification_states")
    if declared_impl != list(IMPLEMENTATION_STATES):
        errors.append("IMPLEMENTATION_STATE_DENOMINATOR_MISMATCH")
    if declared_cert != list(CERTIFICATION_STATES):
        errors.append("CERTIFICATION_STATE_DENOMINATOR_MISMATCH")

    return sorted(set(errors))


def validate_gap_matrix(root: str | Path) -> list[str]:
    root_path = Path(root)
    denominator = load_denominator(root_path)
    return validate_gap_matrix_payload(load_gap_matrix(root_path), denominator=denominator)


def build_video_cli_parity_report(root: str | Path) -> dict[str, Any]:
    root_path = Path(root)
    denominator = load_denominator(root_path)
    gap = load_gap_matrix(root_path)
    errors = sorted(set([*validate_denominator(root_path), *validate_gap_matrix(root_path)]))
    rows = gap.get("rows") if isinstance(gap.get("rows"), list) else []

    implementation_counts = {
        state: sum(
            1
            for row in rows
            if isinstance(row, dict) and row.get("implementation_state") == state
        )
        for state in IMPLEMENTATION_STATES
    }
    certification_counts = {
        state: sum(
            1
            for row in rows
            if isinstance(row, dict) and row.get("certification_state") == state
        )
        for state in CERTIFICATION_STATES
    }
    residue = [
        str(row.get("id"))
        for row in rows
        if isinstance(row, dict)
        and (
            row.get("implementation_state") != "IMPLEMENTED"
            or row.get("certification_state") != "PASS"
        )
    ]

    if errors:
        status = "VIDEO_CLI_PARITY_INVALID"
        certification_state = "FAIL"
    elif residue:
        status = "VIDEO_CLI_PARITY_OPEN"
        certification_state = "OPEN"
    else:
        status = "VIDEO_CLI_PARITY_PASS"
        certification_state = "PASS"

    return {
        "status": status,
        "denominator_id": denominator.get("denominator_id"),
        "baseline_main_sha": denominator.get("baseline_main_sha"),
        "source_sha256": (denominator.get("source_manifest") or {}).get("sha256"),
        "required_capabilities": len(denominator.get("capabilities") or []),
        "implementation_counts": implementation_counts,
        "certification_counts": certification_counts,
        "unresolved_capability_ids": residue,
        "errors": errors,
        "certification_state": certification_state,
    }
