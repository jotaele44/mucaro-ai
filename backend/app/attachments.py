"""Files attached to a chat message for Claude: images, PDFs and documents.

Each upload is written once, immutable, under ``data_dir/attachments/<conversation>/<id>/`` and goes with
the conversation when it is deleted. The stored conversation history holds only a small reference block
(``mucaro_attachment``); ``expand_content`` turns it into the API's image or document block when a request
is built, from the same bytes every time, so a replayed history stays byte-identical.

Limits follow the Messages API (images: JPEG/PNG/GIF/WebP up to 10 MB base64-encoded; a request up to 32 MB;
more than 20 images in one request lowers the per-image size limit). Every attachment is resent with each
later turn, so the budget is per conversation, not per message.
"""

from __future__ import annotations

import base64
import json
import secrets
import shutil
from pathlib import Path, PurePath
from typing import Any

from .config import Settings, settings
from .parser import ParseError, parse_file
from .utils import utc_now

REF_TYPE = "mucaro_attachment"
IMAGE_TYPES = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
}
MAX_IMAGE_BYTES = 7 * 1024 * 1024  # 10 MB once base64-encoded
MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_DOCUMENT_CHARS = 400_000
# Encoded size of all attachments a conversation resends with every request; leaves room in the 32 MB request.
MAX_CONVERSATION_ENCODED_BYTES = 24 * 1024 * 1024
MAX_IMAGES_PER_CONVERSATION = 20
MAX_ATTACHMENTS_PER_MESSAGE = 10
TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json", ".html", ".htm", ".docx", ".eml", ".msg", ".zip", ".py", ".js", ".ts", ".jsx",
                 ".tsx", ".css", ".sql", ".yaml", ".yml", ".toml", ".xml", ".sh", ".log", ".ini", ".cfg", ".rst", ".tex", ".java",
                 ".c", ".h", ".cpp", ".go", ".rs", ".rb", ".php", ".swift", ".kt"}


class AttachmentError(ValueError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _root(conversation_id: str, cfg: Settings) -> Path:
    if not conversation_id.replace("_", "").isalnum():
        raise AttachmentError("ERROR_ATTACHMENT_CONVERSATION_INVALID", "Conversation id is not valid.")
    return cfg.data_dir / "attachments" / conversation_id


def _folder(conversation_id: str, attachment_id: str, cfg: Settings) -> Path:
    if not (attachment_id.startswith("att_") and attachment_id[4:].isalnum()):
        raise AttachmentError("ERROR_ATTACHMENT_NOT_FOUND", "Attachment not found.")
    return _root(conversation_id, cfg) / attachment_id


def _image_type(data: bytes) -> str | None:
    for magic, media_type in IMAGE_TYPES.items():
        if data.startswith(magic):
            return media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _safe_name(name: str | None) -> str:
    raw = PurePath((name or "").replace("\\", "/")).name.strip()
    cleaned = "".join(char for char in raw if char.isprintable() and char not in '<>:"|?*').strip(". ")
    return cleaned[:120] or "attachment"


def _encoded(size: int) -> int:
    return 4 * ((size + 2) // 3)


def _metadata(folder: Path) -> dict[str, Any]:
    return json.loads((folder / "meta.json").read_text(encoding="utf-8"))


def list_attachments(conversation_id: str, cfg: Settings = settings) -> list[dict[str, Any]]:
    root = _root(conversation_id, cfg)
    if not root.is_dir():
        return []
    rows = []
    for folder in sorted(root.iterdir()):
        if (folder / "meta.json").is_file():
            rows.append(_metadata(folder))
    return sorted(rows, key=lambda row: row["created_at"])


def get_attachment(conversation_id: str, attachment_id: str, cfg: Settings = settings) -> dict[str, Any]:
    folder = _folder(conversation_id, attachment_id, cfg)
    if not (folder / "meta.json").is_file():
        raise AttachmentError("ERROR_ATTACHMENT_NOT_FOUND", "Attachment not found.")
    return _metadata(folder)


def attachment_file(conversation_id: str, attachment_id: str, cfg: Settings = settings) -> tuple[Path, dict[str, Any]]:
    meta = get_attachment(conversation_id, attachment_id, cfg)
    return _folder(conversation_id, attachment_id, cfg) / meta["stored_name"], meta


def save_attachment(conversation_id: str, name: str | None, data: bytes, cfg: Settings = settings) -> dict[str, Any]:
    """Check, convert and store one upload. Raises AttachmentError with a plain-language reason."""
    file_name = _safe_name(name)
    suffix = PurePath(file_name).suffix.lower()
    if not data:
        raise AttachmentError("ERROR_ATTACHMENT_EMPTY", f"{file_name} is empty.")
    existing = list_attachments(conversation_id, cfg)
    media_type = _image_type(data)
    text: str | None = None
    truncated = False
    if media_type:
        kind = "image"
        if len(data) > MAX_IMAGE_BYTES:
            raise AttachmentError("ERROR_ATTACHMENT_TOO_LARGE", f"{file_name} is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB; images must be smaller.")
        if sum(1 for row in existing if row["kind"] == "image") >= MAX_IMAGES_PER_CONVERSATION:
            raise AttachmentError("ERROR_ATTACHMENT_LIMIT", f"A conversation can hold up to {MAX_IMAGES_PER_CONVERSATION} images; start a new chat for more.")
    elif data.startswith(b"%PDF-"):
        kind, media_type = "pdf", "application/pdf"
        if len(data) > MAX_PDF_BYTES:
            raise AttachmentError("ERROR_ATTACHMENT_TOO_LARGE", f"{file_name} is larger than {MAX_PDF_BYTES // (1024 * 1024)} MB.")
        if b"/Encrypt" in data[-4096:] or b"/Encrypt" in data[:4096]:
            raise AttachmentError("ERROR_ATTACHMENT_ENCRYPTED", f"{file_name} is password-protected; remove the password and attach it again.")
    elif suffix in TEXT_SUFFIXES or suffix == "":
        kind, media_type = "document", "text/plain"
        if len(data) > MAX_DOCUMENT_BYTES:
            raise AttachmentError("ERROR_ATTACHMENT_TOO_LARGE", f"{file_name} is larger than {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB.")
    else:
        raise AttachmentError(
            "ERROR_ATTACHMENT_UNSUPPORTED",
            f"{file_name}: Claude can read images (PNG, JPEG, GIF, WebP), PDFs and text documents (text, Markdown, CSV, JSON, Word, email, code).",
        )

    attachment_id = f"att_{secrets.token_hex(8)}"
    folder = _folder(conversation_id, attachment_id, cfg)
    folder.mkdir(parents=True, exist_ok=False)
    try:
        stored_name = f"original{suffix if suffix and suffix.isascii() else ''}"
        (folder / stored_name).write_bytes(data)
        if kind == "document":
            try:
                parsed = parse_file(folder / stored_name) if suffix not in {"", ".log"} else {"text": data.decode("utf-8", errors="replace")}
            except ParseError as exc:
                raise AttachmentError("ERROR_ATTACHMENT_UNREADABLE", f"{file_name} could not be read: {exc}") from exc
            text = str(parsed.get("text") or "")
            if not text.strip():
                raise AttachmentError("ERROR_ATTACHMENT_EMPTY", f"{file_name} has no readable text.")
            truncated = len(text) > MAX_DOCUMENT_CHARS
            text = text[:MAX_DOCUMENT_CHARS]
            (folder / "text.txt").write_text(text, encoding="utf-8")
        encoded = len(text.encode("utf-8")) if text is not None else _encoded(len(data))
        used = sum(int(row.get("encoded_bytes") or 0) for row in existing)
        if used + encoded > MAX_CONVERSATION_ENCODED_BYTES:
            raise AttachmentError(
                "ERROR_ATTACHMENT_LIMIT",
                "This conversation's attachments would be too large to send to Claude together; start a new chat for this file.",
            )
        meta = {
            "attachment_id": attachment_id,
            "conversation_id": conversation_id,
            "name": file_name,
            "kind": kind,
            "media_type": media_type,
            "bytes": len(data),
            "encoded_bytes": encoded,
            "stored_name": stored_name,
            "text_truncated": truncated,
            "created_at": utc_now(),
        }
        (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return meta
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def delete_conversation_attachments(conversation_id: str, cfg: Settings = settings) -> None:
    root = _root(conversation_id, cfg)
    if root.is_dir():
        shutil.rmtree(root, ignore_errors=True)


def reference_block(meta: dict[str, Any]) -> dict[str, Any]:
    return {"type": REF_TYPE, "attachment_id": meta["attachment_id"], "name": meta["name"], "kind": meta["kind"]}


def _api_block(conversation_id: str, ref: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    try:
        path, meta = attachment_file(conversation_id, str(ref.get("attachment_id") or ""), cfg)
    except AttachmentError:
        return {"type": "text", "text": f"[Attachment {ref.get('name')} is no longer available.]"}
    folder = path.parent
    if meta["kind"] == "image":
        return {"type": "image", "source": {"type": "base64", "media_type": meta["media_type"], "data": base64.b64encode(path.read_bytes()).decode("ascii")}}
    if meta["kind"] == "pdf":
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": base64.b64encode(path.read_bytes()).decode("ascii")},
            "title": meta["name"],
            "citations": {"enabled": True},
        }
    note = " The text was cut to its first part because the file is long." if meta.get("text_truncated") else ""
    return {
        "type": "document",
        "source": {"type": "text", "media_type": "text/plain", "data": (folder / "text.txt").read_text(encoding="utf-8")},
        "title": meta["name"],
        "context": f"A file the user attached.{note}",
        "citations": {"enabled": True},
    }


def expand_messages(conversation_id: str, messages: list[dict[str, Any]], cfg: Settings = settings) -> list[dict[str, Any]]:
    """Replace attachment references with API content blocks; everything else passes through unchanged."""
    expanded = []
    for message in messages:
        content = message["content"]
        if isinstance(content, list) and any(isinstance(block, dict) and block.get("type") == REF_TYPE for block in content):
            content = [_api_block(conversation_id, block, cfg) if isinstance(block, dict) and block.get("type") == REF_TYPE else block
                       for block in content]
            message = {**message, "content": content}
        expanded.append(message)
    return expanded

