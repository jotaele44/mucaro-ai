from __future__ import annotations

import csv
import json
import re
import zipfile
from email import policy
from email.message import Message
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from .archive_safety import ArchiveSafetyError, inspect_zip, read_member_limited
from .file_signatures import SignatureError, check_signature
from .multimodal import MULTIMODAL_SUFFIXES, parse_multimodal_metadata


class ParseError(RuntimeError):
    def __init__(self, message: str, *, status: str = "ERROR_PARSE_FAILED") -> None:
        super().__init__(message)
        self.status = status


WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
CORE_NS = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
DUBLIN_CORE_NS = "{http://purl.org/dc/elements/1.1/}"
DUBLIN_CORE_TERMS_NS = "{http://purl.org/dc/terms/}"
EMAIL_ATTACHMENT_TEXT_BYTE_LIMIT = 200_000
EMAIL_ATTACHMENT_TEXT_TYPES = {
    "application/json",
    "text/csv",
    "text/html",
    "text/markdown",
    "text/plain",
}
EMAIL_ATTACHMENT_TEXT_SUFFIXES = {".csv", ".htm", ".html", ".json", ".md", ".txt"}
MSG_COMPOUND_FILE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
MSG_TEXT_SEGMENT_LIMIT = 80
DOCX_XML_BYTE_LIMIT = 32 * 1024 * 1024
ZIP_TEXT_MEMBER_SUFFIXES = {".csv", ".json", ".md", ".txt"}
ZIP_TEXT_MEMBER_BYTE_LIMIT = 2 * 1024 * 1024
ZIP_TEXT_TOTAL_BYTE_LIMIT = 20 * 1024 * 1024


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
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".eml": "message/rfc822",
        ".msg": "application/vnd.ms-outlook",
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
        ".zip": "application/zip",
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
        check_signature(path)
    except SignatureError as exc:
        raise ParseError(f"PARSE_FAILED:{path.name}:{exc}", status=exc.status) from exc
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
        if suffix == ".docx":
            return _parse_docx(path)
        if suffix == ".zip":
            return _parse_zip(path)
        if suffix == ".eml":
            return _parse_eml(path)
        if suffix == ".msg":
            return _parse_msg(path)
        if suffix == ".pdf":
            return _parse_pdf(path)
        if suffix in MULTIMODAL_SUFFIXES:
            return parse_multimodal_metadata(path, mime_type=mime)
        raw = path.read_text(encoding="utf-8", errors="replace")
        return {
            "text": raw,
            **_base_result(path, parser="text_fallback", mime_type=mime, warnings=["UNKNOWN_EXTENSION_TEXT_FALLBACK"]),
        }
    except ArchiveSafetyError as exc:
        raise ParseError(f"PARSE_FAILED:{path.name}:{exc}", status=exc.status) from exc
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


def _docx_text(node: ElementTree.Element) -> str:
    parts: list[str] = []
    for element in node.iter():
        if element.tag == f"{WORD_NS}t" and element.text:
            parts.append(element.text)
        elif element.tag == f"{WORD_NS}tab":
            parts.append("\t")
        elif element.tag in {f"{WORD_NS}br", f"{WORD_NS}cr"}:
            parts.append("\n")
    return "".join(parts).strip()


def _docx_core_metadata(archive: zipfile.ZipFile) -> dict[str, str]:
    if "docProps/core.xml" not in archive.namelist():
        return {}
    root = ElementTree.fromstring(read_member_limited(archive, "docProps/core.xml", DOCX_XML_BYTE_LIMIT))
    fields = {
        "title": f"{DUBLIN_CORE_NS}title",
        "creator": f"{DUBLIN_CORE_NS}creator",
        "subject": f"{DUBLIN_CORE_NS}subject",
        "description": f"{DUBLIN_CORE_NS}description",
        "created": f"{DUBLIN_CORE_TERMS_NS}created",
        "modified": f"{DUBLIN_CORE_TERMS_NS}modified",
        "last_modified_by": f"{CORE_NS}lastModifiedBy",
    }
    metadata: dict[str, str] = {}
    for key, tag in fields.items():
        value = root.findtext(tag)
        if value:
            metadata[key] = value
    return metadata


def _parse_docx(path: Path) -> dict[str, Any]:
    warnings: list[str] = []
    safety = inspect_zip(path)
    if safety["status"] != "ARCHIVE_SAFE":
        raise ArchiveSafetyError("ERROR_ARCHIVE_UNSAFE", path.name, safety["findings"])
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if "word/document.xml" not in names:
            warnings.append("DOCX_DOCUMENT_XML_MISSING")
            return {
                "text": "",
                **_base_result(
                    path,
                    parser="docx_text_v1",
                    mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    warnings=warnings,
                    paragraph_count=0,
                    table_count=0,
                    text_length=0,
                    core_properties={},
                ),
            }
        root = ElementTree.fromstring(read_member_limited(archive, "word/document.xml", DOCX_XML_BYTE_LIMIT))
        body = root.find(f".//{WORD_NS}body")
        blocks = list(body) if body is not None else []
        lines: list[str] = []
        for block in blocks:
            if block.tag == f"{WORD_NS}p":
                text = _docx_text(block)
                if text:
                    lines.append(text)
            elif block.tag == f"{WORD_NS}tbl":
                for row in block.findall(f".//{WORD_NS}tr"):
                    cells = [_docx_text(cell) for cell in row.findall(f"./{WORD_NS}tc")]
                    cells = [cell for cell in cells if cell]
                    if cells:
                        lines.append(" | ".join(cells))
        text = "\n".join(lines).strip()
        if not text:
            warnings.append("DOCX_NO_TEXT")
        return {
            "text": text,
            **_base_result(
                path,
                parser="docx_text_v1",
                mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                warnings=warnings,
                paragraph_count=sum(1 for _ in root.iter(f"{WORD_NS}p")),
                table_count=sum(1 for _ in root.iter(f"{WORD_NS}tbl")),
                text_length=len(text),
                core_properties=_docx_core_metadata(archive),
            ),
        }


def _parse_zip(path: Path) -> dict[str, Any]:
    """Inventory a ZIP and extract text members; unsafe archives fail closed."""
    safety = inspect_zip(path)
    if safety["status"] != "ARCHIVE_SAFE":
        raise ArchiveSafetyError(
            "ERROR_ARCHIVE_UNSAFE" if safety["status"] == "ARCHIVE_UNSAFE" else "ERROR_ARCHIVE_MALFORMED",
            path.name,
            safety["findings"],
        )
    warnings: list[str] = []
    sections: list[str] = []
    text_members = 0
    skipped_members = 0
    total = 0
    with zipfile.ZipFile(path) as archive:
        for member in safety["members"]:
            name = member["path"]
            if member["is_dir"]:
                continue
            if Path(name).suffix.lower() not in ZIP_TEXT_MEMBER_SUFFIXES:
                skipped_members += 1
                continue
            data = read_member_limited(archive, name, ZIP_TEXT_MEMBER_BYTE_LIMIT)
            total += len(data)
            if total > ZIP_TEXT_TOTAL_BYTE_LIMIT:
                warnings.append(f"ZIP_TEXT_TRUNCATED:limit={ZIP_TEXT_TOTAL_BYTE_LIMIT}")
                break
            sections.append(f"--- archive member: {name} ---\n{data.decode('utf-8', errors='replace')}")
            text_members += 1
    if skipped_members:
        warnings.append(f"ZIP_NON_TEXT_MEMBERS_NOT_EXTRACTED:{skipped_members}")
    if not sections:
        warnings.append("ZIP_NO_TEXT_MEMBERS")
    text = "\n\n".join(sections)
    return {
        "text": text,
        **_base_result(
            path,
            parser="zip_archive_v1",
            mime_type="application/zip",
            warnings=warnings,
            member_count=safety["member_count"],
            text_member_count=text_members,
            skipped_member_count=skipped_members,
            declared_total_bytes=safety["declared_total_bytes"],
            archive_safety="ARCHIVE_SAFE",
        ),
    }


def _email_headers(message: Message) -> dict[str, str]:
    headers: dict[str, str] = {}
    for source, target in [
        ("subject", "subject"),
        ("from", "from"),
        ("to", "to"),
        ("cc", "cc"),
        ("date", "date"),
        ("message-id", "message_id"),
    ]:
        value = message.get(source)
        if value:
            headers[target] = str(value)
    return headers


def _email_payload_text(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw_payload = part.get_payload()
        return raw_payload if isinstance(raw_payload, str) else ""
    charset = part.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def _email_html_text(raw_html: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(raw_html)
    parser.close()
    return parser.text


def _email_attachment_payload_bytes(part: Message) -> bytes:
    payload = part.get_payload(decode=True)
    if payload is not None:
        return payload
    raw_payload = part.get_payload()
    if isinstance(raw_payload, str):
        charset = part.get_content_charset() or "utf-8"
        return raw_payload.encode(charset, errors="replace")
    return b""


def _email_attachment_is_text_like(filename: str, content_type: str) -> bool:
    suffix = Path(filename).suffix.lower() if filename else ""
    return content_type in EMAIL_ATTACHMENT_TEXT_TYPES or suffix in EMAIL_ATTACHMENT_TEXT_SUFFIXES


def _email_attachment_text(part: Message, *, filename: str, content_type: str) -> tuple[str, str]:
    raw = _email_attachment_payload_bytes(part)
    charset = part.get_content_charset() or "utf-8"
    decoded = raw.decode(charset, errors="replace").strip()
    if content_type == "text/html" or Path(filename).suffix.lower() in {".htm", ".html"}:
        return _email_html_text(decoded).strip(), "email_attachment_html_text_v1"
    if content_type == "application/json" or Path(filename).suffix.lower() == ".json":
        try:
            parsed = json.loads(decoded)
        except json.JSONDecodeError:
            return decoded, "email_attachment_text_v1"
        return json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True), "email_attachment_json_v1"
    return decoded, "email_attachment_text_v1"


def _clean_msg_text_segment(value: str) -> str:
    text = " ".join(value.replace("\x00", " ").split())
    return text if len(text) >= 4 and any(char.isalpha() for char in text) else ""


def _msg_text_segments(raw: bytes) -> list[str]:
    segments: list[str] = []
    seen: set[str] = set()
    patterns = [
        (re.compile(rb"[\x09\x0a\x0d\x20-\x7e]{4,}"), "utf-8"),
        (re.compile(rb"(?:[\x09\x0a\x0d\x20-\x7e]\x00){4,}"), "utf-16le"),
    ]
    for pattern, encoding in patterns:
        for match in pattern.finditer(raw):
            text = _clean_msg_text_segment(match.group(0).decode(encoding, errors="replace"))
            if not text or text in seen:
                continue
            seen.add(text)
            segments.append(text)
            if len(segments) >= MSG_TEXT_SEGMENT_LIMIT:
                return segments
    return segments


def _parse_msg(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    warnings = ["MSG_HEURISTIC_TEXT_EXTRACTION"]
    if not raw.startswith(MSG_COMPOUND_FILE_MAGIC):
        warnings.append("MSG_COMPOUND_MAGIC_MISSING")
    segments = _msg_text_segments(raw)
    if not segments:
        warnings.append("MSG_NO_TEXT_PROBE_MATCH")
    text = "\n".join(segments)
    return {
        "text": text,
        **_base_result(
            path,
            parser="outlook_msg_text_probe_v1",
            mime_type="application/vnd.ms-outlook",
            warnings=warnings,
            text_length=len(text),
            extracted_text_segments=len(segments),
            structured_properties_extracted=False,
            text_segment_limit=MSG_TEXT_SEGMENT_LIMIT,
        ),
    }


def _parse_eml(path: Path) -> dict[str, Any]:
    message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    text_parts: list[str] = []
    html_parts: list[str] = []
    attachment_text_parts: list[str] = []
    attachments: list[dict[str, Any]] = []
    body_part_count = 0
    parts = list(message.walk()) if message.is_multipart() else [message]
    for part in parts:
        if part.is_multipart():
            continue
        content_disposition = (part.get_content_disposition() or "").lower()
        content_type = part.get_content_type()
        filename = part.get_filename()
        if content_disposition == "attachment" or filename:
            filename_text = str(filename or "")
            attachment: dict[str, Any] = {
                "filename": filename_text,
                "content_type": content_type,
                "size_bytes": len(_email_attachment_payload_bytes(part)),
                "extracted": False,
                "skip_reason": "UNSUPPORTED_ATTACHMENT_TYPE",
            }
            if attachment["size_bytes"] > EMAIL_ATTACHMENT_TEXT_BYTE_LIMIT:
                attachment["skip_reason"] = f"ATTACHMENT_TOO_LARGE:limit={EMAIL_ATTACHMENT_TEXT_BYTE_LIMIT}"
            elif _email_attachment_is_text_like(filename_text, content_type):
                extracted_text, attachment_parser = _email_attachment_text(part, filename=filename_text, content_type=content_type)
                if extracted_text:
                    attachment["extracted"] = True
                    attachment["extraction_parser"] = attachment_parser
                    attachment["text_length"] = len(extracted_text)
                    attachment.pop("skip_reason", None)
                    label = filename_text or "unnamed"
                    attachment_text_parts.append(
                        f"[EMAIL ATTACHMENT filename={label} content_type={content_type}]\n{extracted_text}"
                    )
                else:
                    attachment["skip_reason"] = "ATTACHMENT_NO_TEXT"
            attachments.append(attachment)
            continue
        if content_type == "text/plain":
            body_part_count += 1
            text = _email_payload_text(part).strip()
            if text:
                text_parts.append(text)
        elif content_type == "text/html":
            body_part_count += 1
            text = _email_html_text(_email_payload_text(part)).strip()
            if text:
                html_parts.append(text)

    warnings: list[str] = []
    extracted_attachment_count = sum(1 for attachment in attachments if attachment.get("extracted"))
    skipped_attachment_count = len(attachments) - extracted_attachment_count
    if extracted_attachment_count:
        warnings.append(f"EMAIL_ATTACHMENTS_EXTRACTED:{extracted_attachment_count}")
    if skipped_attachment_count:
        warnings.append(f"EMAIL_ATTACHMENTS_SKIPPED:{skipped_attachment_count}")
    if text_parts:
        body_text = "\n\n".join(text_parts)
    elif html_parts:
        body_text = "\n\n".join(html_parts)
        warnings.append("EMAIL_HTML_BODY_USED")
    else:
        body_text = ""
        warnings.append("EMAIL_NO_BODY_TEXT")
    headers = _email_headers(message)
    header_lines = [f"{key}: {value}" for key, value in headers.items()]
    content_sections = [section for section in [body_text, *attachment_text_parts] if section]
    text = "\n".join(header_lines + ([""] if header_lines and content_sections else []) + content_sections).strip()
    return {
        "text": text,
        **_base_result(
            path,
            parser="email_rfc822_v1",
            mime_type="message/rfc822",
            warnings=warnings,
            headers=headers,
            body_part_count=body_part_count,
            attachment_count=len(attachments),
            extracted_attachment_count=extracted_attachment_count,
            skipped_attachment_count=skipped_attachment_count,
            attachments=attachments,
            text_length=len(text),
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
