from __future__ import annotations

import csv
import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import Settings, settings
from .capability_contracts import require_ocr_report_contract
from .local_tools import resolve_tool_request
from .multimodal import IMAGE_SUFFIXES, parse_multimodal_metadata
from .utils import stable_id, utc_now, write_json

OCR_REPORT_SCHEMA_VERSION = "mucaro_ocr_report_v1"
OCR_REPORT_DIR = "ocr_reports"
DEFAULT_OCR_LANGUAGE = "eng"
DEFAULT_LAYOUT_MODE = "single_block"
SUPPORTED_LAYOUT_MODES = {"single_block": "6", "auto": "3"}
CONFIDENCE_SCALE = "0-100"
LOW_CONFIDENCE_THRESHOLD = 50.0
HIGH_CONFIDENCE_THRESHOLD = 85.0

Runner = Callable[[list[str], int], subprocess.CompletedProcess[str]]


class OCRAnalysisError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class OCRSource:
    path: Path
    sha256: str
    metadata: dict[str, Any]


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _report_path(cfg: Settings, report_id: str) -> Path:
    return cfg.export_dir / OCR_REPORT_DIR / f"{report_id}.json"


def _mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".gif": "image/gif",
        ".jpeg": "image/jpeg",
        ".jpg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")


def _load_image_source(path: Path) -> OCRSource:
    if not path.exists() or not path.is_file():
        raise OCRAnalysisError("ERROR_OCR_INPUT_NOT_FOUND", f"OCR input not found: {path}")
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        raise OCRAnalysisError("ERROR_OCR_UNSUPPORTED_INPUT", "OCR analysis currently supports local image files only.")
    metadata = parse_multimodal_metadata(path, mime_type=_mime_type(path))
    return OCRSource(path=path, sha256=_sha256_file(path), metadata=metadata)


def _default_runner(command: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout_seconds)


def _tesseract_version(tesseract_path: str, *, runner: Runner = _default_runner, timeout_seconds: int = 10) -> str:
    completed = runner([tesseract_path, "--version"], timeout_seconds)
    if completed.returncode != 0:
        return "unknown"
    return (completed.stdout or completed.stderr).splitlines()[0].strip() or "unknown"


def _confidence_state(confidence: float | None) -> str:
    if confidence is None:
        return "UNKNOWN"
    if confidence < LOW_CONFIDENCE_THRESHOLD:
        return "LOW"
    if confidence >= HIGH_CONFIDENCE_THRESHOLD:
        return "HIGH"
    return "MEDIUM"


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _parse_confidence(value: str) -> float | None:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    return confidence if confidence >= 0 else None


def _parse_int(value: str | None, default: int = 0) -> int:
    try:
        return int(float(value if value not in {None, ""} else default))
    except (TypeError, ValueError):
        return default


def _parse_tsv(tsv_text: str) -> list[dict[str, Any]]:
    if not tsv_text.strip():
        return []
    rows = csv.DictReader(tsv_text.splitlines(), delimiter="\t")
    spans: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        raw_text = row.get("text", "")
        normalized_text = _normalize_text(raw_text)
        if not normalized_text:
            continue
        confidence = _parse_confidence(row.get("conf", ""))
        span = {
            "span_index": len(spans),
            "raw_text": raw_text,
            "normalized_text": normalized_text,
            "canonical_text": normalized_text.casefold(),
            "confidence": confidence,
            "confidence_state": _confidence_state(confidence),
            "bbox": {
                "x": _parse_int(row.get("left")),
                "y": _parse_int(row.get("top")),
                "width": _parse_int(row.get("width")),
                "height": _parse_int(row.get("height")),
            },
            "page_index": max(_parse_int(row.get("page_num"), 1) - 1, 0),
            "block_num": _parse_int(row.get("block_num")),
            "paragraph_num": _parse_int(row.get("par_num")),
            "line_num": _parse_int(row.get("line_num")),
            "word_num": _parse_int(row.get("word_num"), index + 1),
        }
        spans.append(span)
    return spans


def _write_report(cfg: Settings, report: dict[str, Any]) -> dict[str, Any]:
    path = _report_path(cfg, str(report["report_id"]))
    require_ocr_report_contract(report, report_path=str(path))
    write_json(path, report)
    report_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    return {**report, "report_path": str(path), "report_sha256": report_sha256}


def analyze_image_ocr(
    path: Path,
    *,
    cfg: Settings = settings,
    language: str = DEFAULT_OCR_LANGUAGE,
    layout_mode: str = DEFAULT_LAYOUT_MODE,
    tesseract_path: str | None = None,
    timeout_seconds: int = 30,
    runner: Runner = _default_runner,
) -> dict[str, Any]:
    cfg.ensure_dirs()
    source = _load_image_source(path)
    engine_path = resolve_tool_request("tesseract", tesseract_path)
    if not engine_path:
        raise OCRAnalysisError("ERROR_OCR_ENGINE_UNAVAILABLE", "Tesseract is not bundled in this Mucaro runtime.")
    if language.strip() != language or not language.strip():
        raise OCRAnalysisError("ERROR_OCR_LANGUAGE_INVALID", "OCR language must be a non-empty language code.")
    if layout_mode not in SUPPORTED_LAYOUT_MODES:
        raise OCRAnalysisError("ERROR_OCR_LAYOUT_INVALID", f"Unsupported OCR layout mode: {layout_mode}")
    page_segmentation_mode = SUPPORTED_LAYOUT_MODES[layout_mode]
    command = [engine_path, str(path), "stdout", "--psm", page_segmentation_mode, "-l", language, "tsv"]
    try:
        completed = runner(command, timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise OCRAnalysisError("ERROR_OCR_TIMEOUT", f"OCR analysis exceeded {timeout_seconds} seconds.") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "OCR engine failed.").strip()
        raise OCRAnalysisError("ERROR_OCR_ENGINE_FAILED", detail)
    spans = _parse_tsv(completed.stdout)
    raw_text = "\n".join(span["raw_text"] for span in spans)
    normalized_text = "\n".join(span["normalized_text"] for span in spans)
    low_confidence_count = sum(1 for span in spans if span["confidence_state"] == "LOW")
    unknown_confidence_count = sum(1 for span in spans if span["confidence_state"] == "UNKNOWN")
    warnings = [
        warning
        for warning in [
            "OCR_NO_TEXT_DETECTED" if not spans else "",
            "OCR_LOW_CONFIDENCE_PRESENT" if low_confidence_count else "",
            "OCR_UNKNOWN_CONFIDENCE_PRESENT" if unknown_confidence_count else "",
        ]
        if warning
    ]
    created_at = utc_now()
    report_id = stable_id("ocrreport", "image_ocr", source.sha256, language, layout_mode, created_at)
    report = {
        "schema_version": OCR_REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "status": "OCR_ANALYSIS_OK" if spans else "OCR_ANALYSIS_NO_TEXT",
        "operation": "image_ocr",
        "created_at": created_at,
        "source": {
            "path": str(path),
            "sha256": source.sha256,
            "mime_type": source.metadata.get("mime_type"),
            "media_kind": source.metadata.get("media_kind"),
            "bytes": source.metadata.get("bytes"),
            "image_format": source.metadata.get("image_format"),
            "width": source.metadata.get("width"),
            "height": source.metadata.get("height"),
            "parser_warnings": source.metadata.get("parser_warnings", []),
        },
        "engine": {
            "name": "tesseract",
            "path": engine_path,
            "version": _tesseract_version(engine_path, runner=runner),
            "language": language,
            "layout_mode": layout_mode,
            "page_segmentation_mode": page_segmentation_mode,
            "confidence_scale": CONFIDENCE_SCALE,
        },
        "text": {
            "raw": raw_text,
            "normalized": normalized_text,
            "canonical": normalized_text.casefold(),
        },
        "spans": spans,
        "invariants": {
            "raw_normalized_canonical_separated": True,
            "span_count": len(spans),
            "low_confidence_count": low_confidence_count,
            "unknown_confidence_count": unknown_confidence_count,
            "empty_text_state": not spans,
            "bounding_boxes_have_source_coordinates": all(bool(span["bbox"]) for span in spans),
        },
        "warnings": warnings,
        "limitations": [
            "local OCR currently supports image files only, not PDF page rendering or document containers",
            "layout evidence is word/span level and does not reconstruct tables, forms, handwriting, or reading order beyond engine output",
            "confidence values are engine-reported and require corpus calibration before broad document-intelligence claims",
        ],
        "falsification_criteria": [
            "reject the OCR report if span_count does not equal the number of saved spans",
            "reject text claims if raw, normalized, and canonical text are not preserved as separate fields",
            "treat low or unknown confidence spans as review-required evidence instead of corrected text",
        ],
    }
    return _write_report(cfg, report)
