from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

OCR_DENOMINATOR_SCHEMA = "MUCARO_OCR_V1_CORPUS/1.0"
OCR_EVIDENCE_SCHEMA = "MUCARO_OCR_V1_EVIDENCE/1.0"
OCR_EVIDENCE_REPORT_SCHEMA = "MUCARO_OCR_V1_EVIDENCE_REPORT/1.0"


class OcrCorpusEvidenceError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path, status: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OcrCorpusEvidenceError(status, str(path)) from exc
    if not isinstance(payload, dict):
        raise OcrCorpusEvidenceError(status, f"{path} must contain a JSON object")
    return payload, raw


def _resolve_under(root: Path, relative: Any, *, label: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise OcrCorpusEvidenceError("ERROR_OCR_CORPUS_PATH", label)
    relative_path = Path(relative)
    if ".." in relative_path.parts:
        raise OcrCorpusEvidenceError("ERROR_OCR_CORPUS_PATH_TRAVERSAL", label)
    candidate = root / relative_path
    if candidate.is_symlink():
        raise OcrCorpusEvidenceError("ERROR_OCR_CORPUS_SYMLINK", label)
    target = candidate.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise OcrCorpusEvidenceError("ERROR_OCR_CORPUS_PATH_ESCAPE", label) from exc
    if not target.is_file():
        raise OcrCorpusEvidenceError("ERROR_OCR_CORPUS_FILE_MISSING", label)
    return target


def validate_ocr_v1_evidence(
    denominator_path: str | Path,
    evidence_path: str | Path,
    *,
    evidence_root: str | Path | None = None,
) -> dict[str, Any]:
    denominator_file = Path(denominator_path).expanduser().resolve()
    evidence_file = Path(evidence_path).expanduser().resolve()
    denominator, denominator_bytes = _load_json(denominator_file, "ERROR_OCR_DENOMINATOR_READ")
    evidence, evidence_bytes = _load_json(evidence_file, "ERROR_OCR_EVIDENCE_READ")
    if denominator.get("schema_version") != OCR_DENOMINATOR_SCHEMA:
        raise OcrCorpusEvidenceError("ERROR_OCR_DENOMINATOR_SCHEMA", str(denominator.get("schema_version")))
    if evidence.get("schema_version") != OCR_EVIDENCE_SCHEMA:
        raise OcrCorpusEvidenceError("ERROR_OCR_EVIDENCE_SCHEMA", str(evidence.get("schema_version")))

    denominator_sha = hashlib.sha256(denominator_bytes).hexdigest()
    expected_cases = denominator.get("cases")
    denominator_case_count = denominator.get("case_count")
    if not isinstance(expected_cases, list) or not isinstance(denominator_case_count, int):
        raise OcrCorpusEvidenceError("ERROR_OCR_DENOMINATOR_CASES", "case_count and cases are required")
    if denominator_case_count != len(expected_cases):
        raise OcrCorpusEvidenceError("ERROR_OCR_DENOMINATOR_COUNT", "case_count does not match cases")
    for index, row in enumerate(expected_cases):
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row.get("id"):
            raise OcrCorpusEvidenceError("ERROR_OCR_DENOMINATOR_CASE_ROW", f"cases[{index}]")
        if not isinstance(row.get("expected_state"), str) or not row.get("expected_state"):
            raise OcrCorpusEvidenceError("ERROR_OCR_DENOMINATOR_STATE", str(row.get("id")))

    failures: list[str] = []
    if evidence.get("denominator_sha256") != denominator_sha:
        failures.append("DENOMINATOR_SHA256_MISMATCH")

    evidence_cases = evidence.get("cases")
    if not isinstance(evidence_cases, list):
        raise OcrCorpusEvidenceError("ERROR_OCR_EVIDENCE_CASES", "cases must be an array")

    expected_by_id = {str(row.get("id")): row for row in expected_cases if isinstance(row, dict)}
    if len(expected_by_id) != len(expected_cases):
        raise OcrCorpusEvidenceError("ERROR_OCR_DENOMINATOR_CASE_IDS", "denominator case IDs must be unique")

    root = Path(evidence_root).expanduser().resolve() if evidence_root is not None else evidence_file.parent.resolve()
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(evidence_cases):
        if not isinstance(row, dict):
            raise OcrCorpusEvidenceError("ERROR_OCR_EVIDENCE_CASE_ROW", f"cases[{index}]")
        case_id = row.get("id")
        if not isinstance(case_id, str) or case_id not in expected_by_id:
            failures.append(f"UNKNOWN_CASE:{case_id}")
            continue
        if case_id in seen:
            failures.append(f"DUPLICATE_CASE:{case_id}")
            continue
        seen.add(case_id)
        expected = expected_by_id[case_id]
        source = _resolve_under(root, row.get("source_path"), label=f"{case_id}:source")
        receipt = _resolve_under(root, row.get("receipt_path"), label=f"{case_id}:receipt")
        observed_source_sha = _sha256(source)
        observed_receipt_sha = _sha256(receipt)
        source_match = row.get("source_sha256") == observed_source_sha
        receipt_match = row.get("receipt_sha256") == observed_receipt_sha
        expected_state = expected.get("expected_state")
        observed_state = row.get("observed_state")
        state_match = observed_state == expected_state
        if not source_match:
            failures.append(f"SOURCE_SHA256_MISMATCH:{case_id}")
        if not receipt_match:
            failures.append(f"RECEIPT_SHA256_MISMATCH:{case_id}")
        if not state_match:
            failures.append(f"STATE_MISMATCH:{case_id}:{observed_state}!={expected_state}")
        adjudication = row.get("adjudication")
        if str(expected_state).startswith("UNSUPPORTED_"):
            if adjudication != "EXPECTED_UNSUPPORTED":
                failures.append(f"UNSUPPORTED_ADJUDICATION_REQUIRED:{case_id}")
        elif expected_state == "PROVISIONAL":
            if adjudication not in {"PROVISIONAL", "PASS"}:
                failures.append(f"PROVISIONAL_ADJUDICATION_REQUIRED:{case_id}")
        elif adjudication != "PASS":
            failures.append(f"PASS_ADJUDICATION_REQUIRED:{case_id}")
        rows.append(
            {
                "id": case_id,
                "class": expected.get("class"),
                "expected_state": expected_state,
                "observed_state": observed_state,
                "adjudication": adjudication,
                "source_path": str(source),
                "source_sha256": observed_source_sha,
                "source_identity_match": source_match,
                "receipt_path": str(receipt),
                "receipt_sha256": observed_receipt_sha,
                "receipt_identity_match": receipt_match,
                "state_match": state_match,
            }
        )

    missing = sorted(set(expected_by_id) - seen)
    failures.extend(f"MISSING_CASE:{case_id}" for case_id in missing)
    extra_count = sum(1 for item in failures if item.startswith("UNKNOWN_CASE:"))
    status = "OCR_V1_EVIDENCE_READY" if not failures else "OCR_V1_EVIDENCE_BLOCKED"
    return {
        "schema_version": OCR_EVIDENCE_REPORT_SCHEMA,
        "status": status,
        "certification_ready": not failures,
        "denominator_path": str(denominator_file),
        "denominator_sha256": denominator_sha,
        "evidence_path": str(evidence_file),
        "evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
        "counts": {
            "denominator_cases": len(expected_by_id),
            "evidence_cases": len(evidence_cases),
            "validated_cases": len(rows),
            "missing_cases": len(missing),
            "unknown_cases": extra_count,
            "failures": len(failures),
        },
        "cases": rows,
        "failures": failures,
        "invariants": {
            "denominator_bound_exactly": evidence.get("denominator_sha256") == denominator_sha,
            "case_denominator_closed": not missing and not extra_count and len(seen) == len(expected_by_id),
            "source_identity_closed": all(row["source_identity_match"] for row in rows),
            "receipt_identity_closed": all(row["receipt_identity_match"] for row in rows),
            "expected_states_preserved": all(row["state_match"] for row in rows),
        },
        "limitations": [
            "This ledger proves exact fixture/receipt identity and case accounting; it does not upgrade unsupported OCR classes into supported capability.",
            "Table, form, handwriting and mixed-handwriting cases remain unsupported when the frozen denominator says so.",
            "Engine accuracy thresholds and representative real-world breadth require separate executed evaluation evidence.",
        ],
    }
