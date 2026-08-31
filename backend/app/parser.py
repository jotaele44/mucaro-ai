from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .multimodal import MULTIMODAL_SUFFIXES, parse_multimodal_metadata


class ParseError(RuntimeError):
    pass


def detect_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".csv": "text/csv",
        ".json": "application/json",
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".webm": "video/webm",
    }.get(suffix, "application/octet-stream")


def _base_result(path: Path, *, parser: str, mime_type: str, warnings: list[str] | None = None, **metadata: Any) -> dict[str, Any]:
    parser_warnings = warnings or []
    return {
        "parser": parser,
        "mime_type": mime_type,
        "warnings": parser_warnings,
        "parser_warnings": parser_warnings,
        "parse_status": "PARTIAL" if parser_warnings else "OK",
        "bytes": path.stat().st_size,
        **metadata,
    }


def parse_file(path: Path) -> dict[str, Any]:
    mime = detect_mime(path)
    suffix = path.suffix.lower()
    try:
        if suffix in {".txt", ".md"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            warnings = ["EMPTY_FILE_NO_TEXT"] if not text.strip() else []
            return {"text": text, **_base_result(path, parser="text_utf8", mime_type=mime, warnings=warnings)}
        if suffix == ".csv":
            return _parse_csv(path)
        if suffix == ".json":
            return _parse_json(path)
        if suffix == ".pdf":
            return _parse_pdf(path)
        if suffix in MULTIMODAL_SUFFIXES:
            return parse_multimodal_metadata(path, mime_type=mime)
        raw = path.read_text(encoding="utf-8", errors="replace")
        return {
            "text": raw,
            **_base_result(path, parser="text_fallback", mime_type=mime, warnings=["UNKNOWN_EXTENSION_TEXT_FALLBACK"]),
        }
    except Exception as exc:
        raise ParseError(f"PARSE_FAILED:{path.name}:{exc}") from exc


def _parse_csv(path: Path) -> dict[str, Any]:
    sample = path.read_text(encoding="utf-8", errors="replace")[:8192]
    warnings: list[str] = []
    try:
        dialect = csv.Sniffer().sniff(sample) if sample else csv.excel
    except csv.Error:
        dialect = csv.excel
        warnings.append("CSV_DIALECT_SNIFF_FAILED")
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f, dialect=dialect)
        headers = [str(header) for header in (reader.fieldnames or [])]
        duplicate_headers = sorted({header for header in headers if headers.count(header) > 1})
        if duplicate_headers:
            warnings.append(f"CSV_DUPLICATE_HEADERS:{','.join(duplicate_headers)}")
        for idx, row in enumerate(reader):
            rows.append({str(k): "" if v is None else str(v) for k, v in row.items()})
            if idx + 1 >= 5000:
                warnings.append("CSV_ROWS_TRUNCATED:limit=5000")
                break
    if rows:
        lines = [" | ".join(row.keys())]
        for row in rows:
            lines.append(" | ".join(f"{k}: {v}" for k, v in row.items()))
        text = "\n".join(lines)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "text": text,
        **_base_result(
            path,
            parser="csv_dictreader",
            mime_type="text/csv",
            warnings=warnings,
            delimiter=getattr(dialect, "delimiter", ","),
            headers=headers,
            row_count=len(rows),
        ),
    }


def _parse_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    return {"text": text, **_base_result(path, parser="json_parser", mime_type="application/json")}


def _parse_pdf(path: Path) -> dict[str, Any]:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        return {
            "text": "",
            **_base_result(
                path,
                parser="pdf_unavailable",
                mime_type="application/pdf",
                warnings=["PDF_PARSER_UNAVAILABLE_INSTALL_PYPDF"],
            ),
        }
    reader = PdfReader(str(path))
    pages = []
    warnings: list[str] = []
    for idx, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
            if not text.strip():
                warnings.append(f"PDF_PAGE_EMPTY:{idx + 1}")
            pages.append(f"[PAGE {idx + 1}]\n" + text)
        except Exception as exc:
            warnings.append(f"PDF_PAGE_PARSE_FAILED:{idx + 1}")
            pages.append(f"[PAGE {idx + 1}]\n[PAGE_PARSE_FAILED:{exc}]")
    if not pages:
        warnings.append("PDF_NO_PAGES")
    return {
        "text": "\n\n".join(pages),
        **_base_result(
            path,
            parser="pypdf",
            mime_type="application/pdf",
            warnings=warnings,
            page_count=len(reader.pages),
        ),
    }
