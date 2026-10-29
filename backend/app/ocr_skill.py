"""Governed document OCR: permissioned frozen input, bounded engines, raw lineage."""
from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import tempfile
import warnings
from pathlib import Path
from typing import Any

from PIL import Image
from pypdf import PdfReader

from .artifact_identity import create_artifact_record
from .config import Settings
from .ocr_analysis import analyze_image_ocr
from .ocr_pdf import analyze_pdf_ocr
from .permissions import ensure_directory_permission, is_within_path
from .utils import utc_now, write_json

MAX_PDF_PAGES = 16
MAX_PAGE_PIXELS = 25_000_000
MAX_DOCUMENT_PIXELS = 100_000_000
MAX_TEXT_CHARS = 200_000
OCR_EXECUTION_POLICY = {
    "source_policy": "directory_grant_and_frozen_bytes_v1",
    "max_source_bytes": 25 * 1024 * 1024,
    "max_pdf_pages": MAX_PDF_PAGES,
    "max_page_pixels": MAX_PAGE_PIXELS,
    "max_document_pixels": MAX_DOCUMENT_PIXELS,
    "max_text_chars": MAX_TEXT_CHARS,
    "render_timeout_seconds": 30,
    "image_timeout_seconds": 30,
    "pdf_page_timeout_seconds": 10,
}
OCR_ARGUMENTS_SCHEMA = {
    "type": "object",
    "title": "Read text from an image or PDF",
    "properties": {
        "path": {"type": "string", "minLength": 1, "maxLength": 4096, "pattern": r"\S", "title": "Input file path"},
        "language": {"type": "string", "pattern": r"^[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*$", "maxLength": 80, "default": "eng", "title": "OCR language"},
        "layout_mode": {"type": "string", "enum": ["auto", "single_block"], "default": "auto", "title": "Page layout"},
        "dpi": {"type": "integer", "minimum": 72, "maximum": 300, "default": 200, "title": "PDF resolution (DPI)"},
        "expected_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$", "title": "Expected file SHA-256 (optional)"},
    },
    "required": ["path"],
    "additionalProperties": False,
}


class OCRSkillError(RuntimeError):
    def __init__(self, status: str, detail: str):
        super().__init__(f"{status}: {detail}")
        self.status, self.detail = status, detail


def _check_access(path: Path, cfg: Settings) -> None:
    # App-managed uploads remain usable when user data lives outside the bundle.
    if not is_within_path(path, cfg.input_dir.resolve()):
        ensure_directory_permission(path, cfg)


def _snapshot(requested: str, expected_sha256: str | None, cfg: Settings) -> tuple[Path, dict[str, Any], Any]:
    selected = Path(requested).expanduser()
    source = (selected if selected.is_absolute() else cfg.project_root / selected).resolve(strict=True)
    _check_access(source, cfg)
    if source.suffix.lower() not in {".gif", ".jpeg", ".jpg", ".png", ".webp", ".pdf"}:
        raise OCRSkillError("ERROR_OCR_UNSUPPORTED_INPUT", "Choose an image or PDF.")
    fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    max_bytes = min(cfg.max_upload_bytes, OCR_EXECUTION_POLICY["max_source_bytes"])
    with os.fdopen(fd, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise OCRSkillError("ERROR_OCR_INPUT_NOT_FILE", "OCR input must be a regular file.")
        if before.st_size > max_bytes:
            raise OCRSkillError("ERROR_OCR_INPUT_TOO_LARGE", f"Input exceeds {max_bytes} bytes.")
        raw = handle.read(max_bytes + 1)
        after = os.fstat(handle.fileno())
    current = source.stat(follow_symlinks=False)
    if source.resolve() != source or (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino) or (
        before.st_size, before.st_mtime_ns, before.st_ctime_ns
    ) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise OCRSkillError("ERROR_OCR_SOURCE_CHANGED", "Input changed while being acquired; request OCR again.")
    _check_access(source, cfg)
    if not raw or len(raw) > max_bytes:
        raise OCRSkillError("ERROR_OCR_INPUT_INVALID", "Input is empty or exceeds the byte limit.")
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 and digest != expected_sha256:
        raise OCRSkillError("ERROR_OCR_SOURCE_CHANGED", "Input SHA-256 differs from the requested source identity.")
    cfg.ensure_dirs()
    snapshot_dir = Path(tempfile.mkdtemp(prefix="ocr-snapshot-", dir=cfg.input_dir))
    frozen = snapshot_dir / f"input{source.suffix.lower()}"
    with frozen.open("xb") as output:
        output.write(raw)
    manifest = {
        "requested_path_raw": requested, "resolved_path": str(source), "snapshot_path": str(frozen),
        "retrieval_utc": utc_now(), "bytes": len(raw), "sha256": digest,
    }
    write_json(snapshot_dir / "source_manifest.json", manifest)
    artifact = create_artifact_record(
        processed_dir=cfg.processed_dir, artifact_type="SOURCE", payload=raw,
        logical_identity=f"ocr_source:{frozen}", producer="ocr_skill", producer_version="1.0.0",
        source_path_or_uri=str(frozen), provenance_parameters=manifest,
    )
    return frozen, manifest, artifact


def _check_document_budget(path: Path, dpi: int) -> None:
    if path.suffix == ".pdf":
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            raise OCRSkillError("ERROR_OCR_ENCRYPTED_PDF", "Decrypt the PDF locally before OCR.")
        if not 1 <= len(reader.pages) <= MAX_PDF_PAGES:
            raise OCRSkillError("ERROR_OCR_PAGE_LIMIT", f"OCR accepts 1–{MAX_PDF_PAGES} pages per document; split larger inputs explicitly.")
        total_pixels = 0
        for page in reader.pages:
            unit = float(page.get("/UserUnit", 1))
            width = float(page.mediabox.width) * unit * dpi / 72
            height = float(page.mediabox.height) * unit * dpi / 72
            if not all(math.isfinite(value) and value > 0 for value in (width, height)):
                raise OCRSkillError("ERROR_OCR_DIMENSIONS_INVALID", "PDF page dimensions are invalid.")
            pixels = math.ceil(width) * math.ceil(height)
            total_pixels += pixels
            if pixels > MAX_PAGE_PIXELS or total_pixels > MAX_DOCUMENT_PIXELS:
                raise OCRSkillError("ERROR_OCR_PIXEL_LIMIT", "PDF exceeds the declared page or total raster budget.")
    else:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                if image.width * image.height > MAX_PAGE_PIXELS:
                    raise OCRSkillError("ERROR_OCR_PIXEL_LIMIT", "Image exceeds the raster budget.")
                if getattr(image, "n_frames", 1) != 1:
                    raise OCRSkillError("ERROR_OCR_MULTIFRAME_UNSUPPORTED", "Convert multi-frame images into an explicit PDF or individual pages.")


def analyze_document(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    frozen, manifest, source_artifact = _snapshot(arguments["path"], arguments.get("expected_sha256"), cfg)
    dpi = arguments.get("dpi", 200)
    _check_document_budget(frozen, dpi)
    options = {"cfg": cfg, "language": arguments.get("language", "eng"), "layout_mode": arguments.get("layout_mode", "auto")}
    if frozen.suffix == ".pdf":
        report = analyze_pdf_ocr(frozen, **options, dpi=dpi, render_timeout_seconds=OCR_EXECUTION_POLICY["render_timeout_seconds"], ocr_timeout_seconds=OCR_EXECUTION_POLICY["pdf_page_timeout_seconds"])
        page_reports = []
        for page in report["pages"]:
            payload = Path(page["ocr_report_path"]).read_bytes()
            if hashlib.sha256(payload).hexdigest() != page["ocr_report_sha256"]:
                raise OCRSkillError("ERROR_OCR_REPORT_HASH_MISMATCH", "A page report changed after OCR.")
            page_reports.append(json.loads(payload))
    else:
        report = analyze_image_ocr(frozen, **options, timeout_seconds=OCR_EXECUTION_POLICY["image_timeout_seconds"])
        page_reports = [report]
    if report["source"]["sha256"] != manifest["sha256"]:
        raise OCRSkillError("ERROR_OCR_SOURCE_HASH_MISMATCH", "Engine source differs from the frozen source.")
    report_bytes = Path(report["report_path"]).read_bytes()
    if hashlib.sha256(report_bytes).hexdigest() != report["report_sha256"]:
        raise OCRSkillError("ERROR_OCR_REPORT_HASH_MISMATCH", "Report changed after OCR.")
    report_artifact = create_artifact_record(
        processed_dir=cfg.processed_dir, artifact_type="DERIVED", payload=report_bytes,
        logical_identity=f"ocr_report:{report['report_id']}", producer="ocr_skill", producer_version="1.0.0",
        parent_artifact_ids=[source_artifact.artifact_id], schema_id=report["schema_version"], source_path_or_uri=report["report_path"],
    )
    raw_text = "\n\n".join(page["text"]["raw"] for page in page_reports)
    parser_warnings = list(dict.fromkeys([*report.get("warnings", []), *(warning for page in page_reports for warning in page.get("warnings", []))]))
    truncated = len(raw_text) > MAX_TEXT_CHARS
    if truncated:
        parser_warnings.append("OCR_RESULT_TEXT_TRUNCATED_SEE_PAGE_REPORTS")
    return {
        "status": "OCR_SKILL_OK" if not parser_warnings else "OCR_SKILL_REVIEW_REQUIRED",
        "report_id": report["report_id"], "report_status": report["status"],
        "report_path": report["report_path"], "report_sha256": report["report_sha256"],
        "page_count": len(page_reports), "span_count": sum(len(page["spans"]) for page in page_reports),
        "text": raw_text[:MAX_TEXT_CHARS], "text_truncated": truncated, "warnings": parser_warnings,
        "source_manifest": manifest, "source_artifact_id": source_artifact.artifact_id,
        "artifact_ids": [report_artifact.artifact_id],
        "pages": report.get("pages", []), "limitations": report.get("limitations", []),
        "trust": "UNTRUSTED_CONTENT", "epistemic_classification": "INFERENCE",
    }
