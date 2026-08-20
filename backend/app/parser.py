from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


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
    }.get(suffix, "application/octet-stream")


def parse_file(path: Path) -> dict[str, Any]:
    mime = detect_mime(path)
    suffix = path.suffix.lower()
    try:
        if suffix in {".txt", ".md"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            return {"text": text, "parser": "text_utf8", "mime_type": mime, "warnings": []}
        if suffix == ".csv":
            return _parse_csv(path)
        if suffix == ".json":
            return _parse_json(path)
        if suffix == ".pdf":
            return _parse_pdf(path)
        raw = path.read_text(encoding="utf-8", errors="replace")
        return {"text": raw, "parser": "text_fallback", "mime_type": mime, "warnings": ["UNKNOWN_EXTENSION_TEXT_FALLBACK"]}
    except Exception as exc:
        raise ParseError(f"PARSE_FAILED:{path.name}:{exc}") from exc


def _parse_csv(path: Path) -> dict[str, Any]:
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader):
            rows.append({str(k): "" if v is None else str(v) for k, v in row.items()})
            if idx >= 5000:
                break
    if rows:
        lines = [" | ".join(row.keys())]
        for row in rows:
            lines.append(" | ".join(f"{k}: {v}" for k, v in row.items()))
        text = "\n".join(lines)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    return {"text": text, "parser": "csv_dictreader", "mime_type": "text/csv", "warnings": []}


def _parse_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    return {"text": text, "parser": "json_parser", "mime_type": "application/json", "warnings": []}


def _parse_pdf(path: Path) -> dict[str, Any]:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        return {
            "text": "",
            "parser": "pdf_unavailable",
            "mime_type": "application/pdf",
            "warnings": ["PDF_PARSER_UNAVAILABLE_INSTALL_PYPDF"],
        }
    reader = PdfReader(str(path))
    pages = []
    for idx, page in enumerate(reader.pages):
        try:
            pages.append(f"[PAGE {idx + 1}]\n" + (page.extract_text() or ""))
        except Exception as exc:
            pages.append(f"[PAGE {idx + 1}]\n[PAGE_PARSE_FAILED:{exc}]")
    return {"text": "\n\n".join(pages), "parser": "pypdf", "mime_type": "application/pdf", "warnings": []}
