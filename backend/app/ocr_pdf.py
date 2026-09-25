from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from pypdf import PdfReader

from .config import Settings, settings
from .permission_engine import PermissionEngineError, PermissionRequest, require
from .ocr_analysis import OCRAnalysisError, analyze_image_ocr
from .utils import stable_id, utc_now, write_json

PDF_OCR_SCHEMA_VERSION = "mucaro_pdf_ocr_report_v1"
PDF_OCR_REPORT_DIR = "ocr_reports"
PDF_PAGE_DIR = "ocr_pages"
DEFAULT_DPI = 200
MIN_DPI = 72
MAX_DPI = 600

Runner = Callable[[list[str], int], subprocess.CompletedProcess[str]]


class PDFOCRAnalysisError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _default_runner(command: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout_seconds)


def _renderer_version(renderer_path: str, *, runner: Runner, timeout_seconds: int = 10) -> str:
    completed = runner([renderer_path, "-v"], timeout_seconds)
    text = (completed.stderr or completed.stdout or "").strip()
    return text.splitlines()[0] if text else "unknown"


def _pdf_page_count(path: Path) -> int:
    try:
        reader = PdfReader(str(path))
        return len(reader.pages)
    except Exception as exc:
        raise PDFOCRAnalysisError("ERROR_OCR_PDF_PARSE", f"Unable to read PDF page structure: {exc}") from exc


def _render_pages(
    source: Path,
    output_dir: Path,
    *,
    renderer_path: str,
    dpi: int,
    timeout_seconds: int,
    runner: Runner,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=False)
    prefix = output_dir / "page"
    command = [renderer_path, "-png", "-r", str(dpi), str(source), str(prefix)]
    try:
        completed = runner(command, timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise PDFOCRAnalysisError("ERROR_OCR_PDF_RENDER_TIMEOUT", f"PDF rendering exceeded {timeout_seconds} seconds.") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "PDF renderer failed.").strip()
        raise PDFOCRAnalysisError("ERROR_OCR_PDF_RENDER_FAILED", detail)
    pages = sorted(output_dir.glob("page-*.png"))
    if not pages:
        raise PDFOCRAnalysisError("ERROR_OCR_PDF_RENDER_EMPTY", "PDF renderer produced no page images.")
    return pages


def analyze_pdf_ocr(
    path: Path,
    *,
    cfg: Settings = settings,
    language: str = "eng",
    layout_mode: str = "auto",
    dpi: int = DEFAULT_DPI,
    renderer_path: str | None = None,
    tesseract_path: str | None = None,
    render_timeout_seconds: int = 120,
    ocr_timeout_seconds: int = 60,
    renderer_runner: Runner = _default_runner,
    ocr_runner: Runner = _default_runner,
) -> dict[str, Any]:
    cfg.ensure_dirs()
    source = path.expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise PDFOCRAnalysisError("ERROR_OCR_PDF_NOT_FOUND", f"PDF input not found: {source}")
    if source.suffix.lower() != ".pdf":
        raise PDFOCRAnalysisError("ERROR_OCR_PDF_UNSUPPORTED_INPUT", "PDF OCR requires a .pdf input.")
    if not isinstance(dpi, int) or isinstance(dpi, bool) or dpi < MIN_DPI or dpi > MAX_DPI:
        raise PDFOCRAnalysisError("ERROR_OCR_PDF_DPI", f"dpi must be an integer from {MIN_DPI} through {MAX_DPI}.")

    renderer = renderer_path or shutil.which("pdftoppm")
    if not renderer:
        raise PDFOCRAnalysisError("ERROR_OCR_PDF_RENDERER_UNAVAILABLE", "Local pdftoppm executable was not found.")
    tesseract = tesseract_path or shutil.which("tesseract")
    if not tesseract:
        raise PDFOCRAnalysisError("ERROR_OCR_ENGINE_UNAVAILABLE", "Local tesseract executable was not found.")
    for engine in (renderer, tesseract):
        try:
            require(PermissionRequest(capability="subprocess.execute", subject="ocr_pdf", target=engine, origin="operator"), cfg=cfg)
        except PermissionEngineError as exc:
            raise PDFOCRAnalysisError(exc.status, exc.detail) from exc

    source_sha256 = _sha256_file(source)
    source_bytes = source.stat().st_size
    page_count = _pdf_page_count(source)
    if page_count <= 0:
        raise PDFOCRAnalysisError("ERROR_OCR_PDF_NO_PAGES", "PDF contains no pages.")

    created_at = utc_now()
    report_id = stable_id("ocrpdf", source_sha256, str(dpi), language, layout_mode, created_at)
    page_dir = cfg.export_dir / PDF_PAGE_DIR / report_id
    try:
        page_paths = _render_pages(
            source,
            page_dir,
            renderer_path=renderer,
            dpi=dpi,
            timeout_seconds=render_timeout_seconds,
            runner=renderer_runner,
        )
    except Exception:
        if page_dir.exists():
            shutil.rmtree(page_dir, ignore_errors=True)
        raise

    if len(page_paths) != page_count:
        raise PDFOCRAnalysisError(
            "ERROR_OCR_PDF_PAGE_COUNT_MISMATCH",
            f"PDF declares {page_count} pages but renderer produced {len(page_paths)} images.",
        )

    pages: list[dict[str, Any]] = []
    total_spans = 0
    no_text_pages = 0
    try:
        for page_index, page_path in enumerate(page_paths):
            page_sha256 = _sha256_file(page_path)
            try:
                ocr = analyze_image_ocr(
                    page_path,
                    cfg=cfg,
                    language=language,
                    layout_mode=layout_mode,
                    tesseract_path=tesseract,
                    timeout_seconds=ocr_timeout_seconds,
                    runner=ocr_runner,
                )
            except OCRAnalysisError as exc:
                raise PDFOCRAnalysisError(exc.status, f"page {page_index + 1}: {exc.detail}") from exc
            span_count = int(ocr.get("invariants", {}).get("span_count", 0))
            total_spans += span_count
            if ocr.get("status") == "OCR_ANALYSIS_NO_TEXT":
                no_text_pages += 1
            pages.append(
                {
                    "page_index": page_index,
                    "page_number": page_index + 1,
                    "rendered_path": str(page_path),
                    "rendered_sha256": page_sha256,
                    "rendered_bytes": page_path.stat().st_size,
                    "ocr_report_id": ocr.get("report_id"),
                    "ocr_report_path": ocr.get("report_path"),
                    "ocr_report_sha256": ocr.get("report_sha256"),
                    "ocr_status": ocr.get("status"),
                    "span_count": span_count,
                    "warnings": list(ocr.get("warnings") or []),
                }
            )
    except Exception:
        # Rendered page bytes are retained on OCR failure as forensic input evidence.
        raise

    warnings = []
    if no_text_pages:
        warnings.append("OCR_PDF_NO_TEXT_PAGES_PRESENT")
    report = {
        "schema_version": PDF_OCR_SCHEMA_VERSION,
        "report_id": report_id,
        "status": "OCR_PDF_OK" if not no_text_pages else "OCR_PDF_WARN",
        "operation": "pdf_page_render_ocr",
        "created_at": created_at,
        "source": {
            "path": str(source),
            "sha256": source_sha256,
            "bytes": source_bytes,
            "mime_type": "application/pdf",
            "page_count": page_count,
        },
        "renderer": {
            "name": "pdftoppm",
            "path": renderer,
            "version": _renderer_version(renderer, runner=renderer_runner),
            "dpi": dpi,
            "format": "png",
        },
        "ocr": {
            "engine": "tesseract",
            "path": tesseract,
            "language": language,
            "layout_mode": layout_mode,
        },
        "pages": pages,
        "invariants": {
            "source_page_count": page_count,
            "rendered_page_count": len(page_paths),
            "ocr_page_count": len(pages),
            "page_accounting_closed": page_count == len(page_paths) == len(pages),
            "page_indices_contiguous": [page["page_index"] for page in pages] == list(range(page_count)),
            "all_rendered_page_hashes_present": all(bool(page["rendered_sha256"]) for page in pages),
            "all_ocr_report_hashes_present": all(bool(page["ocr_report_sha256"]) for page in pages),
            "total_span_count": total_spans,
            "no_text_page_count": no_text_pages,
        },
        "warnings": warnings,
        "limitations": [
            "PDF page rasterization depends on a local pdftoppm executable whose version is recorded in the receipt",
            "page OCR retains engine-reported confidence and layout evidence; it does not reconstruct semantic tables, forms, or handwriting",
            "rendered page bytes are derived manifestations and do not replace the original PDF byte identity",
        ],
        "falsification_criteria": [
            "reject when declared PDF page count does not equal rendered and OCR page counts",
            "reject any page without a rendered SHA-256 and OCR report SHA-256",
            "do not substitute native PDF text extraction for OCR when the renderer or OCR engine is unavailable",
            "treat no-text pages as explicit residue rather than silently dropping them",
        ],
    }
    report_path = cfg.export_dir / PDF_OCR_REPORT_DIR / f"{report_id}.json"
    write_json(report_path, report)
    report_sha256 = _sha256_file(report_path)
    return {**report, "report_path": str(report_path), "report_sha256": report_sha256}
