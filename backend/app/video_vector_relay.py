"""VIDEO_VECTOR_RELAY_V1: fail-closed validator and report for the relay capability denominator.

The denominator is frozen from one user-supplied phone recording (identified by SHA-256) of a relay
across several conversations: each answer closes with a NEXT VECTOR block and the operator sends it
back as the next message. This module checks that the committed denominator and gap matrix are
well formed and agree with each other. It makes no capability pass: a row is closed only when it is
IMPLEMENTED with certification PASS, and the report says OPEN until every row is.

Same shape and error vocabulary as ``video_cli_parity`` (VIDEO_CLI_PARITY_V1), plus two controls:
every evidence entry must be a path that exists in the tree, and the matrix records each row's
state at the baseline commit so the change against it stays visible.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

DENOMINATOR_REL = Path("_architecture") / "video_vector_relay_v1.json"
GAP_MATRIX_REL = Path("_architecture") / "video_vector_relay_gap_matrix_v1.json"
SCHEMA_REL = Path("schemas") / "video_vector_relay.schema.json"
FLAGPOLE_REL = Path("_architecture") / "mucaro_flagpole_v1.json"

DENOMINATOR_SCHEMA = "mucaro-video-vector-relay-denominator/1.0"
GAP_SCHEMA = "mucaro-video-vector-relay-gap-matrix/1.0"
DENOMINATOR_ID = "VIDEO_VECTOR_RELAY_V1"
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


class VideoVectorRelayError(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VideoVectorRelayError(f"unable to read JSON artifact: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise VideoVectorRelayError(f"JSON artifact must be an object: {path}")
    return value


def load_denominator(root: str | Path) -> dict[str, Any]:
    return _read_json(Path(root) / DENOMINATOR_REL)


def load_gap_matrix(root: str | Path) -> dict[str, Any]:
    return _read_json(Path(root) / GAP_MATRIX_REL)


def _flagpole_domains(root: Path) -> set[str]:
    domains = _read_json(root / FLAGPOLE_REL).get("domains")
    if not isinstance(domains, list):
        raise VideoVectorRelayError("flagpole domains are missing")
    return {str(row.get("id")) for row in domains if isinstance(row, dict) and isinstance(row.get("id"), str)}


def validate_denominator_payload(payload: dict[str, Any], *, schema: dict[str, Any], allowed_domains: set[str]) -> list[str]:
    errors: list[str] = []
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        return [f"SCHEMA_INVALID:{exc.message}"]
    for error in sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda item: list(item.absolute_path)):
        where = ".".join(str(part) for part in error.absolute_path) or "$"
        errors.append(f"SCHEMA:{where}:{error.message}")
    if payload.get("schema") != DENOMINATOR_SCHEMA:
        errors.append("SCHEMA_MISMATCH")
    if payload.get("denominator_id") != DENOMINATOR_ID:
        errors.append("DENOMINATOR_ID_MISMATCH")

    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, list):
        return sorted({*errors, "DENOMINATOR_SHAPE"})
    for field, label in (("id", "ID"), ("capability", "NAME")):
        for value, count in Counter(row.get(field) for row in capabilities if isinstance(row, dict)).items():
            if value is not None and count > 1:
                errors.append(f"DUPLICATE_CAPABILITY_{label}:{value}")
    for row in capabilities:
        if not isinstance(row, dict):
            errors.append("CAPABILITY_NOT_OBJECT")
            continue
        row_id = row.get("id", "UNKNOWN")
        if row.get("required") is not True:
            errors.append(f"CAPABILITY_NOT_REQUIRED:{row_id}")
        domains = row.get("flagpole_domains")
        for domain in domains if isinstance(domains, list) else []:
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


def _state_counts(rows: list[Any], field: str) -> dict[str, int]:
    computed = Counter(row.get(field) for row in rows if isinstance(row, dict) and row.get(field) in IMPLEMENTATION_STATES)
    counts = {state: computed[state] for state in IMPLEMENTATION_STATES}
    counts["TOTAL"] = len(rows)
    return counts


def validate_gap_matrix_payload(gap: dict[str, Any], *, denominator: dict[str, Any], root: Path | None = None) -> list[str]:
    """root, when given, makes every evidence entry answer to the tree: a path that does not exist is an error."""
    errors: list[str] = []
    if gap.get("schema") != GAP_SCHEMA:
        errors.append("GAP_SCHEMA_MISMATCH")
    if gap.get("denominator_id") != denominator.get("denominator_id"):
        errors.append("GAP_DENOMINATOR_ID_MISMATCH")
    if gap.get("baseline_main_sha") != denominator.get("baseline_main_sha"):
        errors.append("GAP_BASELINE_SHA_MISMATCH")

    rows = gap.get("rows")
    if not isinstance(rows, list):
        return sorted({*errors, "GAP_MATRIX_SHAPE"})
    denominator_ids = {row.get("id") for row in denominator.get("capabilities", []) if isinstance(row, dict)}
    row_ids = [row.get("id") for row in rows if isinstance(row, dict)]
    for value, count in Counter(row_ids).items():
        if value is not None and count > 1:
            errors.append(f"DUPLICATE_GAP_ID:{value}")
    errors.extend(f"GAP_ROW_MISSING:{missing}" for missing in sorted(denominator_ids - set(row_ids)))
    errors.extend(f"GAP_ROW_UNKNOWN:{extra}" for extra in sorted(set(row_ids) - denominator_ids))

    for row in rows:
        if not isinstance(row, dict):
            errors.append("GAP_ROW_NOT_OBJECT")
            continue
        row_id = row.get("id", "UNKNOWN")
        state = row.get("implementation_state")
        if state not in IMPLEMENTATION_STATES:
            errors.append(f"UNKNOWN_IMPLEMENTATION_STATE:{row_id}:{state}")
        if row.get("baseline_state") not in IMPLEMENTATION_STATES:
            errors.append(f"UNKNOWN_BASELINE_STATE:{row_id}:{row.get('baseline_state')}")
        if row.get("certification_state") not in CERTIFICATION_STATES:
            errors.append(f"UNKNOWN_CERTIFICATION_STATE:{row_id}:{row.get('certification_state')}")
        evidence = row.get("evidence")
        if not isinstance(evidence, list) or not all(isinstance(item, str) and item for item in evidence):
            errors.append(f"INVALID_EVIDENCE:{row_id}")
            evidence = []
        if state == "IMPLEMENTED" and not evidence:
            errors.append(f"IMPLEMENTED_WITHOUT_EVIDENCE:{row_id}")
        if root is not None:
            for item in evidence:
                relative = Path(item)
                if relative.is_absolute() or ".." in relative.parts or not (root / relative).is_file():
                    errors.append(f"EVIDENCE_PATH_MISSING:{row_id}:{item}")
        if not isinstance(row.get("gap"), str) or not row.get("gap", "").strip():
            errors.append(f"GAP_TEXT_MISSING:{row_id}")

    if gap.get("counts") != _state_counts(rows, "implementation_state"):
        errors.append("COUNT_MISMATCH")
    if gap.get("baseline_counts") != _state_counts(rows, "baseline_state"):
        errors.append("BASELINE_COUNT_MISMATCH")
    if gap.get("implementation_states") != list(IMPLEMENTATION_STATES):
        errors.append("IMPLEMENTATION_STATE_DENOMINATOR_MISMATCH")
    if gap.get("certification_states") != list(CERTIFICATION_STATES):
        errors.append("CERTIFICATION_STATE_DENOMINATOR_MISMATCH")
    return sorted(set(errors))


def validate_gap_matrix(root: str | Path) -> list[str]:
    root_path = Path(root)
    return validate_gap_matrix_payload(load_gap_matrix(root_path), denominator=load_denominator(root_path), root=root_path)


def build_video_vector_relay_report(root: str | Path) -> dict[str, Any]:
    root_path = Path(root)
    denominator = load_denominator(root_path)
    gap = load_gap_matrix(root_path)
    errors = sorted({*validate_denominator(root_path), *validate_gap_matrix(root_path)})
    rows = [row for row in gap.get("rows") or [] if isinstance(row, dict)]
    residue = [
        str(row.get("id")) for row in rows
        if row.get("implementation_state") != "IMPLEMENTED" or row.get("certification_state") != "PASS"
    ]
    if errors:
        status, certification_state = "VIDEO_VECTOR_RELAY_INVALID", "FAIL"
    elif residue:
        status, certification_state = "VIDEO_VECTOR_RELAY_OPEN", "OPEN"
    else:
        status, certification_state = "VIDEO_VECTOR_RELAY_PASS", "PASS"
    return {
        "status": status,
        "denominator_id": denominator.get("denominator_id"),
        "baseline_main_sha": denominator.get("baseline_main_sha"),
        "source_sha256": (denominator.get("source_manifest") or {}).get("sha256"),
        "required_capabilities": len(denominator.get("capabilities") or []),
        "baseline_counts": {state: sum(1 for row in rows if row.get("baseline_state") == state) for state in IMPLEMENTATION_STATES},
        "implementation_counts": {state: sum(1 for row in rows if row.get("implementation_state") == state) for state in IMPLEMENTATION_STATES},
        "certification_counts": {state: sum(1 for row in rows if row.get("certification_state") == state) for state in CERTIFICATION_STATES},
        "unresolved_capability_ids": residue,
        "errors": errors,
        "certification_state": certification_state,
    }
