from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import Settings, settings
from .utils import stable_id, utc_now, write_json

OCR_PAGE_RENDER_SCHEMA_VERSION = "mucaro_ocr_page_render_v1"
DEFAULT_DPI = 200
DEFAULT_MAX_PAGES = 250
DEFAULT_MAX_PAGE_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 512 * 1024 * 1024


class OCRDocumentError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class RenderedPage:
    page_index: int
    width: int
    height: int
    png_bytes: bytes


@dataclass(frozen=True)
class RenderResult:
    engine: str
    engine_version: str
    pages: tuple[RenderedPage, ...]


Renderer = Callable[[Path, int], RenderResult]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _default_renderer(path: Path, dpi: int) -> RenderResult:
    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise OCRDocumentError(
            "ERROR_OCR_PDF_RENDERER_UNAVAILABLE",
            "PyMuPDF is not installed; PDF rendering remains unavailable until the optional local renderer is installed.",
        ) from exc

    try:
        doc = fitz.open(path)
    except Exception as exc:
        raise OCRDocumentError("ERROR_OCR_PDF_OPEN", str(exc)) from exc
    try:
        scale = dpi / 72.0
        matrix = fitz.Matrix(scale, scale)
        pages: list[RenderedPage] = []
        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            pages.append(
                RenderedPage(
                    page_index=page_index,
                    width=int(pix.width),
                    height=int(pix.height),
                    png_bytes=bytes(pix.tobytes("png")),
                )
            )
        version = str(getattr(fitz, "VersionBind", "unknown"))
        return RenderResult(engine="pymupdf", engine_version=version, pages=tuple(pages))
    finally:
        doc.close()


def render_pdf_pages(
    path: Path,
    *,
    cfg: Settings = settings,
    dpi: int = DEFAULT_DPI,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_page_bytes: int = DEFAULT_MAX_PAGE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    renderer: Renderer = _default_renderer,
) -> dict[str, Any]:
    cfg.ensure_dirs()
    source = path.expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise OCRDocumentError("ERROR_OCR_PDF_NOT_FOUND", str(source))
    if source.suffix.lower() != ".pdf":
        raise OCRDocumentError("ERROR_OCR_PDF_INPUT_REQUIRED", "PDF page rendering requires a .pdf source.")
    for label, value in (
        ("dpi", dpi),
        ("max_pages", max_pages),
        ("max_page_bytes", max_page_bytes),
        ("max_total_bytes", max_total_bytes),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise OCRDocumentError("ERROR_OCR_PDF_LIMIT", f"{label} must be a positive integer")

    raw_source = source.read_bytes()
    source_sha256 = _sha256_bytes(raw_source)
    if not raw_source.startswith(b"%PDF-"):
        raise OCRDocumentError("ERROR_OCR_PDF_SIGNATURE", "source does not begin with a PDF signature")

    rendered = renderer(source, dpi)
    pages = list(rendered.pages)
    if len(pages) > max_pages:
        raise OCRDocumentError("ERROR_OCR_PDF_PAGE_LIMIT", f"page_count={len(pages)} limit={max_pages}")
    if [page.page_index for page in pages] != list(range(len(pages))):
        raise OCRDocumentError("ERROR_OCR_PDF_PAGE_INDEX", "rendered page indexes must be contiguous, zero-based, and unique")

    total_bytes = 0
    page_rows: list[dict[str, Any]] = []
    created_at = utc_now()
    manifest_id = stable_id("ocrpages", source_sha256, str(dpi), rendered.engine, rendered.engine_version, created_at)
    page_dir = cfg.export_dir / "ocr_pages" / manifest_id
    page_dir.mkdir(parents=True, exist_ok=False)

    try:
        for page in pages:
            if page.width <= 0 or page.height <= 0:
                raise OCRDocumentError("ERROR_OCR_PDF_PAGE_DIMENSIONS", f"page_index={page.page_index}")
            byte_size = len(page.png_bytes)
            if byte_size > max_page_bytes:
                raise OCRDocumentError("ERROR_OCR_PDF_PAGE_BYTES", f"page_index={page.page_index} bytes={byte_size}")
            total_bytes += byte_size
            if total_bytes > max_total_bytes:
                raise OCRDocumentError("ERROR_OCR_PDF_TOTAL_BYTES", f"total_bytes={total_bytes}")
            if not page.png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                raise OCRDocumentError("ERROR_OCR_PDF_PAGE_FORMAT", f"page_index={page.page_index} is not PNG")
            output = page_dir / f"page-{page.page_index + 1:04d}.png"
            output.write_bytes(page.png_bytes)
            page_rows.append(
                {
                    "page_index": page.page_index,
                    "output_path": str(output),
                    "sha256": _sha256_bytes(page.png_bytes),
                    "byte_size": byte_size,
                    "width": page.width,
                    "height": page.height,
                }
            )
    except Exception:
        for child in page_dir.glob("*"):
            if child.is_file():
                child.unlink()
        page_dir.rmdir()
        raise

    manifest = {
        "schema_version": OCR_PAGE_RENDER_SCHEMA_VERSION,
        "manifest_id": manifest_id,
        "status": "OCR_PDF_RENDER_OK" if page_rows else "OCR_PDF_RENDER_EMPTY",
        "created_at": created_at,
        "source": {
            "path": str(source),
            "sha256": source_sha256,
            "byte_size": len(raw_source),
            "content_type": "application/pdf",
        },
        "renderer": {
            "engine": rendered.engine,
            "engine_version": rendered.engine_version,
            "dpi": dpi,
        },
        "page_count": len(page_rows),
        "pages": page_rows,
        "invariants": {
            "page_indexes_contiguous": [row["page_index"] for row in page_rows] == list(range(len(page_rows))),
            "page_count_closed": len(page_rows) == len(pages),
            "page_hashes_present": all(bool(row["sha256"]) for row in page_rows),
            "total_rendered_bytes": total_bytes,
            "source_preserved": source.exists() and _sha256_bytes(source.read_bytes()) == source_sha256,
        },
        "limitations": [
            "PDF rendering creates page-image evidence only; OCR text extraction is a separate operation",
            "renderer availability is optional and local; no cloud provider is invoked",
            "rendered PNG identity is a source manifestation and is not semantic identity with the PDF page",
        ],
        "falsification_criteria": [
            "reject non-PDF signatures",
            "reject non-contiguous or duplicate page indexes",
            "reject non-PNG render outputs",
            "reject page or total byte-limit overflow rather than truncate output",
            "reject if the source PDF hash changes during rendering",
        ],
    }
    manifest_path = page_dir / "manifest.json"
    write_json(manifest_path, manifest)
    return {
        **manifest,
        "manifest_path": str(manifest_path),
        "manifest_sha256": _sha256_bytes(manifest_path.read_bytes()),
    }
