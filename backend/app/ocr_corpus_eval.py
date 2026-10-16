from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import Settings, settings
from .ocr_analysis import OCRAnalysisError, analyze_image_ocr
from .ocr_corpus_evidence import OcrCorpusEvidenceError, validate_ocr_v1_evidence
from .ocr_pdf import PDFOCRAnalysisError, analyze_pdf_ocr
from .release_receipts import write_release_receipt
from .utils import sha256_file, stable_id, utc_now, write_json

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OCR_CORPUS_MANIFEST = ROOT / "_architecture" / "ocr_v1_representative_corpus.json"
OCR_CORPUS_EVAL_SCHEMA = "MUCARO_OCR_V1_CORPUS_EVAL/1.0"
OCR_CORPUS_EVAL_DIR = "ocr_evals"
OCR_CORPUS_READY = "OCR_REPRESENTATIVE_CORPUS_READY"
OCR_CORPUS_FAILED = "OCR_REPRESENTATIVE_CORPUS_FAILED"
OCR_CORPUS_RECEIPT_TYPE = "ocr_representative_corpus"

CommandRunner = Callable[[list[str], int], subprocess.CompletedProcess[str]]


class OcrCorpusEvaluationError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _load_json(path: Path, status: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OcrCorpusEvaluationError(status, str(path)) from exc
    if not isinstance(payload, dict):
        raise OcrCorpusEvaluationError(status, str(path))
    return payload, raw


def _resolve_source(root: Path, relative: Any, *, label: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise OcrCorpusEvaluationError("ERROR_OCR_CORPUS_SOURCE_PATH", label)
    relative_path = Path(relative)
    if ".." in relative_path.parts:
        raise OcrCorpusEvaluationError("ERROR_OCR_CORPUS_SOURCE_TRAVERSAL", label)
    candidate = root / relative_path
    if candidate.is_symlink():
        raise OcrCorpusEvaluationError("ERROR_OCR_CORPUS_SOURCE_SYMLINK", label)
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise OcrCorpusEvaluationError("ERROR_OCR_CORPUS_SOURCE_ESCAPE", label) from exc
    if not resolved.is_file():
        raise OcrCorpusEvaluationError("ERROR_OCR_CORPUS_SOURCE_MISSING", label)
    return resolved


def load_ocr_corpus_manifest(
    manifest_path: str | Path = DEFAULT_OCR_CORPUS_MANIFEST,
    *,
    project_root: str | Path = ROOT,
) -> tuple[dict[str, Any], str]:
    path = Path(manifest_path).expanduser().resolve()
    root = Path(project_root).expanduser().resolve()
    manifest, raw = _load_json(path, "ERROR_OCR_CORPUS_MANIFEST_READ")
    if manifest.get("schema_version") != "MUCARO_OCR_V1_CORPUS/1.0":
        raise OcrCorpusEvaluationError("ERROR_OCR_CORPUS_MANIFEST_SCHEMA", str(manifest.get("schema_version")))
    cases = manifest.get("cases")
    case_count = manifest.get("case_count")
    if not isinstance(cases, list) or not isinstance(case_count, int) or case_count != len(cases):
        raise OcrCorpusEvaluationError("ERROR_OCR_CORPUS_MANIFEST_COUNT", "case_count does not equal cases length")
    seen: set[str] = set()
    for index, row in enumerate(cases):
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row.get("id"):
            raise OcrCorpusEvaluationError("ERROR_OCR_CORPUS_MANIFEST_CASE", f"cases[{index}]")
        case_id = row["id"]
        if case_id in seen:
            raise OcrCorpusEvaluationError("ERROR_OCR_CORPUS_MANIFEST_DUPLICATE", case_id)
        seen.add(case_id)
        source = _resolve_source(root, row.get("source_path"), label=case_id)
        expected_sha = row.get("source_sha256")
        if not isinstance(expected_sha, str) or expected_sha != sha256_file(source):
            raise OcrCorpusEvaluationError("ERROR_OCR_CORPUS_MANIFEST_SHA256", case_id)
        if row.get("input_kind") not in {"image", "pdf"}:
            raise OcrCorpusEvaluationError("ERROR_OCR_CORPUS_MANIFEST_INPUT_KIND", case_id)
        if row.get("expected_state") not in {"SUPPORTED", "PROVISIONAL", "NO_TEXT"}:
            raise OcrCorpusEvaluationError("ERROR_OCR_CORPUS_MANIFEST_STATE", case_id)
        if not isinstance(row.get("required_tokens"), list):
            raise OcrCorpusEvaluationError("ERROR_OCR_CORPUS_MANIFEST_TOKENS", case_id)
    return manifest, hashlib.sha256(raw).hexdigest()


def _default_runner(command: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout_seconds)


def _portable_ocr_runner(command: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    """Handle a macOS Tesseract build that cannot open absolute image paths."""
    argv = list(command)
    cwd: str | None = None
    if argv and Path(argv[0]).name == "tesseract" and len(argv) > 1:
        candidate = Path(argv[1])
        if candidate.is_absolute() and candidate.is_file():
            cwd = str(candidate.parent)
            argv[1] = candidate.name
    return subprocess.run(argv, check=False, capture_output=True, text=True, timeout=timeout_seconds, cwd=cwd)


def _portable_renderer_runner(command: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    argv = list(command)
    cwd: str | None = None
    if argv and Path(argv[0]).name == "pdftoppm" and len(argv) > 4:
        candidate = Path(argv[4])
        if candidate.is_absolute() and candidate.is_file():
            cwd = str(candidate.parent)
            argv[4] = candidate.name
    return subprocess.run(argv, check=False, capture_output=True, text=True, timeout=timeout_seconds, cwd=cwd)


def _relative_path(path: str | Path, root: Path, *, label: str) -> str:
    candidate = Path(path).expanduser().resolve()
    try:
        return str(candidate.relative_to(root.resolve()))
    except ValueError as exc:
        raise OcrCorpusEvaluationError("ERROR_OCR_CORPUS_ARTIFACT_ESCAPE", label) from exc


def _canonical_whitespace(value: str) -> str:
    return " ".join(value.split())


def _load_pdf_text(report: dict[str, Any]) -> tuple[str, list[str], int, bool]:
    texts: list[str] = []
    warnings: list[str] = list(report.get("warnings", [])) if isinstance(report.get("warnings"), list) else []
    pages = report.get("pages") if isinstance(report.get("pages"), list) else []
    span_count = 0
    child_accounting_closed = True
    for page in pages:
        if not isinstance(page, dict):
            child_accounting_closed = False
            continue
        span_count += int(page.get("span_count", 0) or 0)
        child_path = page.get("ocr_report_path")
        if not isinstance(child_path, str):
            child_accounting_closed = False
            continue
        try:
            child = json.loads(Path(child_path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            child_accounting_closed = False
            continue
        text = child.get("text", {})
        if isinstance(text, dict):
            texts.append(str(text.get("normalized", "")))
        child_warnings = child.get("warnings")
        if isinstance(child_warnings, list):
            warnings.extend(str(item) for item in child_warnings)
        child_accounting_closed = child_accounting_closed and int(child.get("invariants", {}).get("span_count", -1)) == len(child.get("spans", []))
    invariants = report.get("invariants", {})
    page_accounting_closed = bool(invariants.get("page_accounting_closed"))
    return "\n".join(texts), sorted(set(warnings)), span_count, page_accounting_closed and child_accounting_closed


def _evaluate_case(row: dict[str, Any], report: dict[str, Any], source: Path, root: Path) -> dict[str, Any]:
    input_kind = str(row["input_kind"])
    report_status = str(report.get("status", ""))
    if input_kind == "image":
        observed_text = str(report.get("text", {}).get("normalized", ""))
        warnings = [str(item) for item in report.get("warnings", []) if isinstance(item, str)]
        span_count = int(report.get("invariants", {}).get("span_count", 0) or 0)
        span_accounting_closed = span_count == len(report.get("spans", [])) and bool(
            report.get("invariants", {}).get("bounding_boxes_have_source_coordinates", False)
        )
        expected_statuses = {"OCR_ANALYSIS_OK", "OCR_ANALYSIS_NO_TEXT"}
        page_count_match = True
    else:
        observed_text, warnings, span_count, span_accounting_closed = _load_pdf_text(report)
        expected_statuses = {"OCR_PDF_OK", "OCR_PDF_WARN"}
        expected_page_count = row.get("expected_page_count")
        page_count_match = expected_page_count is None or report.get("source", {}).get("page_count") == expected_page_count

    expected_state = str(row["expected_state"])
    observed_state = "NO_TEXT" if report_status == "OCR_ANALYSIS_NO_TEXT" else expected_state if report_status in expected_statuses else "ERROR"
    source_match = report.get("source", {}).get("sha256") == row.get("source_sha256")
    required_tokens = [str(token) for token in row.get("required_tokens", [])]
    normalized_observed = observed_text.casefold()
    token_hits = [token for token in required_tokens if token.casefold() in normalized_observed]
    token_recall = len(token_hits) / len(required_tokens) if required_tokens else 1.0
    required_token_recall = float(row.get("minimum_required_token_recall", 1.0))
    missing_warnings = sorted({str(item) for item in row.get("required_warnings", [])} - set(warnings))
    accent_tokens = [str(token) for token in row.get("accent_tokens", [])]
    missing_accent_tokens = [token for token in accent_tokens if token.casefold() not in normalized_observed]
    exact_expected = row.get("expected_normalized_text")
    exact_match = exact_expected is None or _canonical_whitespace(observed_text) == _canonical_whitespace(str(exact_expected))
    no_text_match = expected_state != "NO_TEXT" or (report_status == "OCR_ANALYSIS_NO_TEXT" and span_count == 0)
    status_match = report_status in expected_statuses and (expected_state != "NO_TEXT" or report_status == "OCR_ANALYSIS_NO_TEXT")
    quality_flags = []
    if not source_match:
        quality_flags.append("SOURCE_IDENTITY_MISMATCH")
    if not status_match:
        quality_flags.append("STATUS_MISMATCH")
    if token_recall < required_token_recall:
        quality_flags.append("REQUIRED_TOKEN_RECALL_BELOW_THRESHOLD")
    if missing_warnings:
        quality_flags.append("REQUIRED_WARNING_MISSING")
    if not no_text_match:
        quality_flags.append("NO_TEXT_STATE_MISMATCH")
    if not exact_match:
        quality_flags.append("EXACT_NORMALIZED_TEXT_MISMATCH")
    if not page_count_match:
        quality_flags.append("PAGE_COUNT_MISMATCH")
    if not span_accounting_closed:
        quality_flags.append("SPAN_ACCOUNTING_NOT_CLOSED")
    if missing_accent_tokens:
        quality_flags.append("ACCENT_PRESERVATION_NOT_PROVEN")
    return {
        "id": row["id"],
        "class": row.get("class"),
        "input_kind": input_kind,
        "source_path": row["source_path"],
        "source_sha256": sha256_file(source),
        "report_path": _relative_path(report["report_path"], root, label=f"{row['id']}:report"),
        "report_sha256": report.get("report_sha256"),
        "report_status": report_status,
        "expected_state": expected_state,
        "observed_state": observed_state,
        "adjudication": row.get("adjudication"),
        "observed_text": observed_text,
        "required_tokens": required_tokens,
        "matched_tokens": token_hits,
        "missing_tokens": sorted(set(required_tokens) - set(token_hits)),
        "token_recall": token_recall,
        "missing_accent_tokens": missing_accent_tokens,
        "missing_warnings": missing_warnings,
        "span_count": span_count,
        "source_identity_match": source_match,
        "status_match": status_match,
        "page_count_match": page_count_match,
        "exact_normalized_text_match": exact_match,
        "span_accounting_closed": span_accounting_closed,
        "quality_flags": quality_flags,
        "bounded_observation_pass": not any(
            flag != "ACCENT_PRESERVATION_NOT_PROVEN" for flag in quality_flags
        ),
    }


def build_ocr_corpus_evaluation(
    project_root: str | Path = ROOT,
    *,
    cfg: Settings = settings,
    manifest_path: str | Path = DEFAULT_OCR_CORPUS_MANIFEST,
    tesseract_path: str | None = None,
    renderer_path: str | None = None,
    image_runner: CommandRunner = _portable_ocr_runner,
    renderer_runner: CommandRunner = _portable_renderer_runner,
    ocr_runner: CommandRunner = _portable_ocr_runner,
    command: list[str] | None = None,
    write_receipt: bool = True,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    manifest, manifest_sha256 = load_ocr_corpus_manifest(manifest_path, project_root=root)
    cfg.ensure_dirs()
    started_at = utc_now()
    head_proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, text=True, capture_output=True, check=False)
    head_sha = head_proc.stdout.strip() if head_proc.returncode == 0 else "UNKNOWN_HEAD"
    report_id = stable_id("ocrcorpus", manifest_sha256, head_sha, started_at)
    results: list[dict[str, Any]] = []
    execution_failures: list[dict[str, str]] = []

    for row in manifest["cases"]:
        source = _resolve_source(root, row["source_path"], label=str(row["id"]))
        try:
            if row["input_kind"] == "image":
                report = analyze_image_ocr(
                    source,
                    cfg=cfg,
                    language=str(row.get("language", "eng")),
                    layout_mode=str(row.get("layout_mode", "single_block")),
                    tesseract_path=tesseract_path,
                    runner=image_runner,
                )
            else:
                report = analyze_pdf_ocr(
                    source,
                    cfg=cfg,
                    language=str(row.get("language", "eng")),
                    layout_mode=str(row.get("layout_mode", "auto")),
                    dpi=int(row.get("dpi", 200)),
                    renderer_path=renderer_path,
                    tesseract_path=tesseract_path,
                    renderer_runner=renderer_runner,
                    ocr_runner=ocr_runner,
                )
            results.append(_evaluate_case(row, report, source, root))
        except (OCRAnalysisError, PDFOCRAnalysisError, OcrCorpusEvaluationError) as exc:
            execution_failures.append({"id": str(row["id"]), "status": getattr(exc, "status", "ERROR_OCR_CORPUS_CASE"), "detail": getattr(exc, "detail", str(exc))})

    eval_dir = cfg.export_dir / OCR_CORPUS_EVAL_DIR
    evidence_path = eval_dir / f"{report_id}-evidence.json"
    report_path = eval_dir / f"{report_id}.json"
    evidence_validation: dict[str, Any] | None = None
    if not execution_failures and len(results) == len(manifest["cases"]):
        evidence = {
            "schema_version": "MUCARO_OCR_V1_EVIDENCE/1.0",
            "denominator_sha256": manifest_sha256,
            "cases": [
                {
                    "id": result["id"],
                    "source_path": result["source_path"],
                    "source_sha256": result["source_sha256"],
                    "receipt_path": result["report_path"],
                    "receipt_sha256": result["report_sha256"],
                    "observed_state": result["observed_state"],
                    "adjudication": result["adjudication"],
                }
                for result in results
            ],
        }
        write_json(evidence_path, evidence)
        try:
            evidence_validation = validate_ocr_v1_evidence(
                manifest_path,
                evidence_path,
                evidence_root=root,
            )
        except OcrCorpusEvidenceError as exc:
            evidence_validation = {"status": exc.status, "certification_ready": False, "failures": [exc.detail]}

    quality_failures = [
        {"id": row["id"], "flags": row["quality_flags"]}
        for row in results
        if row.get("quality_flags")
    ]
    all_bounded_observations_pass = bool(results) and not execution_failures and all(
        row.get("bounded_observation_pass") is True for row in results
    )
    evidence_ready = bool(evidence_validation and evidence_validation.get("certification_ready") is True)
    accuracy_certified = all_bounded_observations_pass and all(row.get("expected_state") != "PROVISIONAL" for row in results)
    quality_state = "PASS" if accuracy_certified else "PROVISIONAL"
    status = OCR_CORPUS_READY if evidence_ready and not execution_failures else OCR_CORPUS_FAILED
    report = {
        "schema_version": OCR_CORPUS_EVAL_SCHEMA,
        "status": status,
        "report_id": report_id,
        "created_at": started_at,
        "head_sha": head_sha,
        "project_root": str(root),
        "manifest_path": _relative_path(manifest_path, root, label="manifest"),
        "manifest_sha256": manifest_sha256,
        "scope": "BOUNDED_REPRESENTATIVE_CALIBRATION_AND_REGRESSION",
        "quality_state": quality_state,
        "accuracy_certified": accuracy_certified,
        "evidence_path": _relative_path(evidence_path, root, label="evidence") if evidence_path.exists() else None,
        "case_results": results,
        "execution_failures": execution_failures,
        "quality_failures": quality_failures,
        "metrics": {
            "case_count": len(manifest["cases"]),
            "executed_case_count": len(results),
            "bounded_observation_pass_rate": sum(row.get("bounded_observation_pass") is True for row in results) / len(results) if results else 0.0,
            "required_token_recall_mean": sum(float(row.get("token_recall", 0.0)) for row in results) / len(results) if results else 0.0,
            "source_identity_match_rate": sum(row.get("source_identity_match") is True for row in results) / len(results) if results else 0.0,
            "span_accounting_closed_rate": sum(row.get("span_accounting_closed") is True for row in results) / len(results) if results else 0.0,
            "accent_preservation_case_count": sum(not row.get("missing_accent_tokens") for row in results),
        },
        "evidence_validation": evidence_validation,
        "invariants": {
            "manifest_case_count_closed": len(manifest["cases"]) == int(manifest["case_count"]),
            "all_cases_executed": len(results) == len(manifest["cases"]) and not execution_failures,
            "evidence_ledger_ready": evidence_ready,
            "source_identity_closed": bool(results) and all(row.get("source_identity_match") is True for row in results),
            "span_accounting_closed": bool(results) and all(row.get("span_accounting_closed") is True for row in results),
            "provisional_cases_not_promoted": not accuracy_certified or all(row.get("expected_state") != "PROVISIONAL" for row in results),
        },
        "limitations": manifest.get("limitations", []),
        "falsification_criteria": [
            "fail when a manifest source SHA-256 differs from the committed source bytes",
            "fail when any case is missing, duplicated, or has an unbound OCR receipt",
            "fail when source or receipt identity does not close",
            "keep quality_state PROVISIONAL while expected observations or confidence calibration remain incomplete",
            "do not infer semantic tables, forms, handwriting, or universal accuracy from this corpus",
        ],
    }
    write_json(report_path, report)
    report["report_path"] = str(report_path)
    report["report_sha256"] = sha256_file(report_path)
    if write_receipt and head_sha != "UNKNOWN_HEAD":
        receipt = write_release_receipt(
            root,
            receipt_type=OCR_CORPUS_RECEIPT_TYPE,
            status=status,
            head_sha=head_sha,
            command=command or ["mucaro", "certify", "ocr-corpus"],
            payload={
                "manifest_path": report["manifest_path"],
                "manifest_sha256": manifest_sha256,
                "report_path": report["report_path"],
                "report_sha256": report["report_sha256"],
                "evidence_path": report["evidence_path"],
                "quality_state": quality_state,
                "accuracy_certified": accuracy_certified,
                "metrics": report["metrics"],
                "invariants": report["invariants"],
                "quality_failures": quality_failures,
            },
        )
        report["receipt"] = str(receipt.relative_to(root) if receipt.is_relative_to(root) else receipt)
    return report
