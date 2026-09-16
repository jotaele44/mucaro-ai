from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .config import Settings, settings

CODING_CERTIFICATION_SCHEMA_VERSION = "mucaro-coding-certification/1.0"
ALLOWED_EVIDENCE_STATES = {"PASS", "FAIL", "BLOCKED", "UNRESOLVED"}
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class CodingCertificationError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _stable_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _manifest_path(cfg: Settings) -> Path:
    return cfg.project_root / "_architecture" / "coding_regression_manifest.json"


def load_coding_regression_manifest(cfg: Settings = settings) -> dict[str, Any]:
    path = _manifest_path(cfg)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CodingCertificationError("ERROR_CODING_CERTIFICATION_MANIFEST_READ", str(path)) from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CodingCertificationError("ERROR_CODING_CERTIFICATION_MANIFEST_PARSE", str(path)) from exc
    requirements = payload.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        raise CodingCertificationError("ERROR_CODING_CERTIFICATION_MANIFEST_EMPTY", str(path))
    ids: list[str] = []
    for row in requirements:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
            raise CodingCertificationError("ERROR_CODING_CERTIFICATION_MANIFEST_ROW", "every requirement needs an id")
        ids.append(row["id"])
    if len(ids) != len(set(ids)):
        raise CodingCertificationError("ERROR_CODING_CERTIFICATION_MANIFEST_DUPLICATE", "requirement IDs must be unique")
    return {
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "payload": payload,
    }


def _normalize_evidence_rows(evidence_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not isinstance(evidence_rows, list):
        raise CodingCertificationError("ERROR_CODING_CERTIFICATION_EVIDENCE", "evidence_rows must be a list")
    normalized: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(evidence_rows):
        if not isinstance(row, dict):
            raise CodingCertificationError("ERROR_CODING_CERTIFICATION_EVIDENCE_ROW", f"evidence_rows[{index}] must be an object")
        requirement_id = row.get("requirement_id")
        state = row.get("state")
        evidence = row.get("evidence", [])
        if not isinstance(requirement_id, str) or not requirement_id:
            raise CodingCertificationError("ERROR_CODING_CERTIFICATION_EVIDENCE_ID", f"evidence_rows[{index}]")
        if requirement_id in normalized:
            raise CodingCertificationError("ERROR_CODING_CERTIFICATION_EVIDENCE_DUPLICATE", requirement_id)
        if state not in ALLOWED_EVIDENCE_STATES:
            raise CodingCertificationError("ERROR_CODING_CERTIFICATION_EVIDENCE_STATE", f"{requirement_id}:{state}")
        if not isinstance(evidence, list) or not all(isinstance(item, str) and item for item in evidence):
            raise CodingCertificationError("ERROR_CODING_CERTIFICATION_EVIDENCE_LIST", requirement_id)
        if state == "PASS" and not evidence:
            raise CodingCertificationError("ERROR_CODING_CERTIFICATION_PASS_WITHOUT_EVIDENCE", requirement_id)
        normalized[requirement_id] = {
            "requirement_id": requirement_id,
            "state": state,
            "evidence": evidence,
            "detail": row.get("detail"),
            "evidence_sha256": _stable_hash(evidence),
        }
    return normalized


def build_coding_certification_report(
    evidence_rows: list[dict[str, Any]],
    *,
    source_head_sha: str | None = None,
    runtime_identity_sha256: str | None = None,
    coding_image_digest: str | None = None,
    cfg: Settings = settings,
) -> dict[str, Any]:
    manifest = load_coding_regression_manifest(cfg)
    requirements = manifest["payload"]["requirements"]
    requirement_ids = {row["id"] for row in requirements}
    supplied = _normalize_evidence_rows(evidence_rows)
    unknown = sorted(set(supplied) - requirement_ids)
    if unknown:
        raise CodingCertificationError("ERROR_CODING_CERTIFICATION_UNKNOWN_REQUIREMENT", ",".join(unknown))

    rows: list[dict[str, Any]] = []
    for requirement in requirements:
        requirement_id = requirement["id"]
        evidence = supplied.get(requirement_id)
        if evidence is None:
            evidence = {
                "requirement_id": requirement_id,
                "state": "UNRESOLVED",
                "evidence": [],
                "detail": "No evidence row supplied.",
                "evidence_sha256": _stable_hash([]),
            }
        rows.append(
            {
                "requirement_id": requirement_id,
                "class": requirement.get("class"),
                "gate": requirement.get("gate"),
                "state": evidence["state"],
                "evidence": evidence["evidence"],
                "detail": evidence.get("detail"),
                "evidence_sha256": evidence["evidence_sha256"],
            }
        )

    counts = {state: sum(row["state"] == state for row in rows) for state in ("PASS", "FAIL", "BLOCKED", "UNRESOLVED")}
    row_accounting_closed = len(rows) == sum(counts.values())
    all_gates_pass = counts == {"PASS": len(rows), "FAIL": 0, "BLOCKED": 0, "UNRESOLVED": 0}
    source_identity_valid = bool(source_head_sha and SHA40.fullmatch(source_head_sha))
    runtime_identity_valid = bool(runtime_identity_sha256 and SHA64.fullmatch(runtime_identity_sha256))
    image_identity_valid = bool(coding_image_digest and IMAGE_DIGEST.fullmatch(coding_image_digest))
    identity_closed = source_identity_valid and runtime_identity_valid and image_identity_valid
    certification_ready = bool(all_gates_pass and row_accounting_closed and identity_closed)

    if certification_ready:
        status = "CODING_CERTIFICATION_READY"
    elif counts["FAIL"]:
        status = "CODING_CERTIFICATION_FAILED"
    elif counts["BLOCKED"]:
        status = "CODING_CERTIFICATION_BLOCKED"
    else:
        status = "CODING_CERTIFICATION_PROVISIONAL"

    residue = [
        {
            "requirement_id": row["requirement_id"],
            "state": row["state"],
            "gate": row["gate"],
        }
        for row in rows
        if row["state"] != "PASS"
    ]
    identity_residue: list[str] = []
    if not source_identity_valid:
        identity_residue.append("SOURCE_HEAD_IDENTITY")
    if not runtime_identity_valid:
        identity_residue.append("RUNTIME_IDENTITY")
    if not image_identity_valid:
        identity_residue.append("CODING_IMAGE_IDENTITY")

    payload: dict[str, Any] = {
        "schema_version": CODING_CERTIFICATION_SCHEMA_VERSION,
        "status": status,
        "certification_ready": certification_ready,
        "manifest": {
            "path": manifest["path"],
            "sha256": manifest["sha256"],
            "schema_version": manifest["payload"].get("schema_version"),
            "requirement_count": len(rows),
        },
        "identity": {
            "source_head_sha": source_head_sha,
            "runtime_identity_sha256": runtime_identity_sha256,
            "coding_image_digest": coding_image_digest,
            "source_identity_valid": source_identity_valid,
            "runtime_identity_valid": runtime_identity_valid,
            "image_identity_valid": image_identity_valid,
            "identity_closed": identity_closed,
        },
        "state_counts": counts,
        "row_count": len(rows),
        "rows": rows,
        "material_residue_count": len(residue) + len(identity_residue),
        "material_residue": residue,
        "identity_residue": identity_residue,
        "invariants": {
            "row_accounting_closed": row_accounting_closed,
            "manifest_identity_frozen": bool(manifest["sha256"]),
            "no_unknown_requirements": not unknown,
            "every_requirement_has_terminal_state": all(row["state"] in ALLOWED_EVIDENCE_STATES for row in rows),
            "pass_requires_evidence": all(row["state"] != "PASS" or bool(row["evidence"]) for row in rows),
            "certification_requires_all_pass": not certification_ready or all_gates_pass,
            "certification_requires_identity_closure": not certification_ready or identity_closed,
        },
        "certification_statement_allowed": certification_ready,
        "allowed_certification_statement": "MUCARO AI CODING PLANE CERTIFIED" if certification_ready else None,
        "falsification_criteria": [
            "any FAIL, BLOCKED or UNRESOLVED gate prevents certification readiness",
            "missing source HEAD, runtime identity or pinned coding image identity prevents certification readiness",
            "unknown or duplicate requirement evidence fails closed",
            "PASS without non-empty evidence fails closed",
            "manifest arithmetic must close exactly before any certification statement is allowed",
        ],
    }
    payload["report_sha256"] = _stable_hash(payload)
    return payload
