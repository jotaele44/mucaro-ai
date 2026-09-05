from __future__ import annotations

import csv
import json
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .multimodal import MULTIMODAL_SUFFIXES, parse_multimodal_metadata


class ParseError(RuntimeError):
    pass


class _HTMLTextExtractor(HTMLParser):
    _BLOCK_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
    _SKIP_TAGS = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._title_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self.link_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
            return
        if tag == "a" and any(name.lower() == "href" and value for name, value in attrs):
            self.link_count += 1
        if tag in self._BLOCK_TAGS:
            self._append_break()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
            return
        if tag in self._BLOCK_TAGS:
            self._append_break()

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        if self._in_title:
            self._title_parts.append(text)
            return
        if self._skip_depth:
            return
        self._parts.append(text)

    def _append_break(self) -> None:
        if self._parts and self._parts[-1] != "\n":
            self._parts.append("\n")

    @property
    def text(self) -> str:
        lines = []
        current: list[str] = []
        for part in self._parts:
            if part == "\n":
                if current:
                    lines.append(" ".join(current).strip())
                    current = []
                continue
            current.append(part)
        if current:
            lines.append(" ".join(current).strip())
        return "\n".join(line for line in lines if line).strip()

    @property
    def title(self) -> str:
        return " ".join(self._title_parts).strip()


def detect_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".html": "text/html",
        ".htm": "text/html",
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
        if suffix in {".html", ".htm"}:
            return _parse_html(path)
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


def _parse_html(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    parser = _HTMLTextExtractor()
    parser.feed(raw)
    parser.close()
    warnings = ["HTML_NO_TEXT"] if not parser.text else []
    return {
        "text": parser.text,
        **_base_result(
            path,
            parser="html_text_v1",
            mime_type="text/html",
            warnings=warnings,
            title=parser.title,
            link_count=parser.link_count,
            text_length=len(parser.text),
        ),
    }


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
