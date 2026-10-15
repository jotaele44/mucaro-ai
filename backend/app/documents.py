from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any
from uuid import uuid4

from .config import Settings, settings
from .db import get_connection

ALLOWED_BLOCK_TYPES = {
    "title", "paragraph", "heading", "code", "bullets", "ordered",
    "quote", "checklist", "image", "divider", "section",
}
ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,120}$")
MAX_TABS = 32
MAX_BLOCKS_PER_TAB = 5000
MAX_DOCUMENT_BYTES = 5_000_000
MAX_ASSET_BYTES = 5_000_000
ALLOWED_ASSET_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


class DocumentError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _checked_id(value: str, label: str) -> str:
    if not ID_RE.fullmatch(value or ""):
        raise DocumentError("ERROR_DOCUMENT_INVALID_ID", f"Invalid {label}.")
    return value


def _validate_block(block: dict[str, Any], seen: set[str]) -> dict[str, Any]:
    if not isinstance(block, dict):
        raise DocumentError("ERROR_DOCUMENT_INVALID_BLOCK", "Each block must be an object.")
    block_id = _checked_id(str(block.get("block_id", "")), "block_id")
    if block_id in seen:
        raise DocumentError("ERROR_DOCUMENT_DUPLICATE_BLOCK_ID", f"Duplicate block_id {block_id}.")
    seen.add(block_id)
    kind = str(block.get("type", ""))
    if kind not in ALLOWED_BLOCK_TYPES:
        raise DocumentError("ERROR_DOCUMENT_INVALID_BLOCK_TYPE", f"Unsupported block type {kind!r}.")
    out = deepcopy(block)
    if kind in {"title", "paragraph", "heading", "code", "quote"}:
        text = out.get("text", "")
        if not isinstance(text, str):
            raise DocumentError("ERROR_DOCUMENT_INVALID_BLOCK", f"{kind} text must be a string.")
        out["text"] = text
        marks = out.get("marks", [])
        if not isinstance(marks, list):
            raise DocumentError("ERROR_DOCUMENT_INVALID_MARKS", "Inline marks must be an array.")
        normalized_marks = []
        for mark in marks:
            if not isinstance(mark, dict) or mark.get("type") not in {"bold", "italic", "underline", "strike"}:
                raise DocumentError("ERROR_DOCUMENT_INVALID_MARKS", "Unsupported inline mark.")
            start, end = mark.get("start"), mark.get("end")
            if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start or end > len(text):
                raise DocumentError("ERROR_DOCUMENT_INVALID_MARK_RANGE", "Inline mark range is outside the block text.")
            normalized_marks.append({"type": mark["type"], "start": start, "end": end})
        out["marks"] = sorted(normalized_marks, key=lambda row: (row["start"], row["end"], row["type"]))
        if kind == "heading":
            level = int(out.get("level", 2))
            if level not in {1, 2, 3}:
                raise DocumentError("ERROR_DOCUMENT_INVALID_HEADING", "Heading level must be 1, 2, or 3.")
            out["level"] = level
    elif kind in {"bullets", "ordered"}:
        items = out.get("items", [])
        if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
            raise DocumentError("ERROR_DOCUMENT_INVALID_LIST", "List items must be strings.")
        out["items"] = items
    elif kind == "checklist":
        items = out.get("items", [])
        if not isinstance(items, list):
            raise DocumentError("ERROR_DOCUMENT_INVALID_CHECKLIST", "Checklist items must be an array.")
        normalized = []
        item_ids: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                raise DocumentError("ERROR_DOCUMENT_INVALID_CHECKLIST", "Checklist entries must be objects.")
            item_id = _checked_id(str(item.get("item_id", "")), "checklist item_id")
            if item_id in item_ids:
                raise DocumentError("ERROR_DOCUMENT_DUPLICATE_CHECKLIST_ID", f"Duplicate checklist item {item_id}.")
            item_ids.add(item_id)
            text = item.get("text", "")
            if not isinstance(text, str):
                raise DocumentError("ERROR_DOCUMENT_INVALID_CHECKLIST", "Checklist item text must be a string.")
            normalized.append({"item_id": item_id, "text": text, "checked": bool(item.get("checked", False))})
        out["items"] = normalized
    elif kind == "image":
        src = out.get("src", "")
        if not isinstance(src, str) or not src:
            raise DocumentError("ERROR_DOCUMENT_INVALID_IMAGE", "Image src is required.")
        if src.startswith(("http://", "https://", "//")):
            raise DocumentError("ERROR_DOCUMENT_REMOTE_IMAGE", "Persistent document images must use local/content-addressed sources.")
        caption = out.get("caption", "")
        if not isinstance(caption, str):
            raise DocumentError("ERROR_DOCUMENT_INVALID_IMAGE", "Image caption must be a string.")
        out["caption"] = caption
    elif kind == "section":
        title = out.get("title", "")
        if not isinstance(title, str):
            raise DocumentError("ERROR_DOCUMENT_INVALID_SECTION", "Section title must be a string.")
        children = out.get("children", [])
        if not isinstance(children, list):
            raise DocumentError("ERROR_DOCUMENT_INVALID_SECTION", "Section children must be an array of block IDs.")
        out["children"] = [_checked_id(str(child), "section child block_id") for child in children]
    return out


def validate_document_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise DocumentError("ERROR_DOCUMENT_INVALID", "Document payload must be an object.")
    title = payload.get("title", "")
    if not isinstance(title, str) or not title.strip():
        raise DocumentError("ERROR_DOCUMENT_TITLE_REQUIRED", "Document title is required.")
    if len(title) > 240:
        raise DocumentError("ERROR_DOCUMENT_TITLE_TOO_LONG", "Document title must be at most 240 characters.")
    tabs = payload.get("tabs")
    if not isinstance(tabs, list) or not tabs:
        raise DocumentError("ERROR_DOCUMENT_TAB_REQUIRED", "At least one document tab is required.")
    if len(tabs) > MAX_TABS:
        raise DocumentError("ERROR_DOCUMENT_TOO_MANY_TABS", f"Documents support at most {MAX_TABS} tabs.")
    seen_tabs: set[str] = set()
    normalized_tabs = []
    global_blocks: set[str] = set()
    for position, tab in enumerate(tabs):
        if not isinstance(tab, dict):
            raise DocumentError("ERROR_DOCUMENT_INVALID_TAB", "Each tab must be an object.")
        tab_id = _checked_id(str(tab.get("tab_id", "")), "tab_id")
        if tab_id in seen_tabs:
            raise DocumentError("ERROR_DOCUMENT_DUPLICATE_TAB_ID", f"Duplicate tab_id {tab_id}.")
        seen_tabs.add(tab_id)
        tab_title = tab.get("title", "")
        if not isinstance(tab_title, str) or not tab_title.strip():
            raise DocumentError("ERROR_DOCUMENT_INVALID_TAB", "Each tab requires a title.")
        blocks = tab.get("blocks", [])
        if not isinstance(blocks, list) or len(blocks) > MAX_BLOCKS_PER_TAB:
            raise DocumentError("ERROR_DOCUMENT_INVALID_BLOCKS", f"Each tab supports at most {MAX_BLOCKS_PER_TAB} blocks.")
        normalized_blocks = [_validate_block(block, global_blocks) for block in blocks]
        block_ids = {block["block_id"] for block in normalized_blocks}
        for block in normalized_blocks:
            if block["type"] == "section":
                missing = [child for child in block["children"] if child not in block_ids]
                if missing:
                    raise DocumentError("ERROR_DOCUMENT_SECTION_CHILD_MISSING", f"Section references missing block IDs: {missing}.")
        normalized_tabs.append({"tab_id": tab_id, "title": tab_title.strip(), "position": position, "blocks": normalized_blocks})
    out = {
        "title": title.strip(),
        "tabs": normalized_tabs,
        "metadata": deepcopy(payload.get("metadata") or {}),
        "view": deepcopy(payload.get("view") or {}),
    }
    encoded = _canonical_bytes(out)
    if len(encoded) > MAX_DOCUMENT_BYTES:
        raise DocumentError("ERROR_DOCUMENT_TOO_LARGE", f"Document exceeds {MAX_DOCUMENT_BYTES} bytes.")
    return out


def _document_row(row: Any) -> dict[str, Any]:
    payload = json.loads(row["payload_json"])
    return {
        "document_id": row["document_id"],
        "title": row["title"],
        "revision": int(row["current_revision"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "document": payload,
    }


def create_document(
    title: str,
    *,
    origin_type: str = "manual",
    origin_ref: str | None = None,
    initial_text: str = "",
    cfg: Settings = settings,
) -> dict[str, Any]:
    doc_id = _new_id("doc")
    tab_id = _new_id("tab")
    blocks = []
    if initial_text:
        blocks.append({"block_id": _new_id("blk"), "type": "paragraph", "text": initial_text})
    payload = validate_document_payload(
        {
            "title": title,
            "tabs": [{"tab_id": tab_id, "title": "Tab 1", "blocks": blocks}],
            "metadata": {"origin_type": origin_type, "origin_ref": origin_ref},
            "view": {},
        }
    )
    now = _now()
    encoded = _canonical_bytes(payload)
    digest = sha256(encoded).hexdigest()
    with get_connection(cfg.sqlite_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO documents (document_id, title, current_revision, created_at, updated_at, payload_json) VALUES (?, ?, 1, ?, ?, ?)",
            (doc_id, payload["title"], now, now, encoded.decode("utf-8")),
        )
        conn.execute(
            "INSERT INTO document_revisions (document_id, revision, parent_revision, created_at, actor, content_sha256, payload_json) VALUES (?, 1, NULL, ?, ?, ?, ?)",
            (doc_id, now, "api", digest, encoded.decode("utf-8")),
        )
        conn.commit()
    return get_document(doc_id, cfg=cfg)


def list_documents(*, cfg: Settings = settings) -> list[dict[str, Any]]:
    with get_connection(cfg.sqlite_path) as conn:
        rows = conn.execute(
            "SELECT document_id, title, current_revision, created_at, updated_at, payload_json FROM documents ORDER BY updated_at DESC, document_id ASC"
        ).fetchall()
    return [_document_row(row) for row in rows]


def get_document(document_id: str, *, cfg: Settings = settings) -> dict[str, Any]:
    _checked_id(document_id, "document_id")
    with get_connection(cfg.sqlite_path) as conn:
        row = conn.execute(
            "SELECT document_id, title, current_revision, created_at, updated_at, payload_json FROM documents WHERE document_id = ?",
            (document_id,),
        ).fetchone()
    if not row:
        raise DocumentError("ERROR_DOCUMENT_NOT_FOUND", "Document not found.")
    return _document_row(row)


def update_document(
    document_id: str,
    payload: dict[str, Any],
    expected_revision: int,
    *,
    actor: str = "api",
    cfg: Settings = settings,
) -> dict[str, Any]:
    _checked_id(document_id, "document_id")
    if expected_revision < 1:
        raise DocumentError("ERROR_DOCUMENT_REVISION", "Expected revision must be at least 1.")
    normalized = validate_document_payload(payload)
    encoded = _canonical_bytes(normalized)
    digest = sha256(encoded).hexdigest()
    now = _now()
    with get_connection(cfg.sqlite_path) as conn:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT current_revision, payload_json FROM documents WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            if not row:
                raise DocumentError("ERROR_DOCUMENT_NOT_FOUND", "Document not found.")
            current = int(row["current_revision"])
            if current != expected_revision:
                raise DocumentError("ERROR_DOCUMENT_REVISION_CONFLICT", f"Expected revision {expected_revision}, found {current}.")
            if row["payload_json"] == encoded.decode("utf-8"):
                conn.execute("ROLLBACK")
                return get_document(document_id, cfg=cfg)
            next_revision = current + 1
            conn.execute(
                "UPDATE documents SET title = ?, current_revision = ?, updated_at = ?, payload_json = ? WHERE document_id = ? AND current_revision = ?",
                (normalized["title"], next_revision, now, encoded.decode("utf-8"), document_id, current),
            )
            conn.execute(
                "INSERT INTO document_revisions (document_id, revision, parent_revision, created_at, actor, content_sha256, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (document_id, next_revision, current, now, actor, digest, encoded.decode("utf-8")),
            )
            # Comments never silently migrate after edits. Missing anchors or now-invalid
            # character ranges become ORPHANED and retain their original binding evidence.
            blocks = {
                block["block_id"]: block
                for tab in normalized["tabs"]
                for block in tab["blocks"]
            }
            comment_rows = conn.execute(
                "SELECT comment_id, payload_json FROM document_comments WHERE document_id = ? AND state = 'ACTIVE'",
                (document_id,),
            ).fetchall()
            for comment_row in comment_rows:
                comment = json.loads(comment_row["payload_json"])
                anchor = blocks.get(comment.get("block_id"))
                orphaned = anchor is None
                if not orphaned and comment.get("start_offset") is not None:
                    anchor_text = str(anchor.get("text", ""))
                    orphaned = int(comment.get("end_offset") or 0) > len(anchor_text)
                if orphaned:
                    comment["state"] = "ORPHANED"
                    comment["updated_at"] = now
                    conn.execute(
                        "UPDATE document_comments SET state = 'ORPHANED', updated_at = ?, payload_json = ? WHERE comment_id = ?",
                        (now, json.dumps(comment, ensure_ascii=False, sort_keys=True), comment_row["comment_id"]),
                    )
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
    return get_document(document_id, cfg=cfg)


def list_revisions(document_id: str, *, cfg: Settings = settings) -> list[dict[str, Any]]:
    _checked_id(document_id, "document_id")
    with get_connection(cfg.sqlite_path) as conn:
        rows = conn.execute(
            "SELECT revision, parent_revision, created_at, actor, content_sha256 FROM document_revisions WHERE document_id = ? ORDER BY revision DESC",
            (document_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def create_comment(
    document_id: str,
    *,
    tab_id: str,
    block_id: str,
    text: str,
    start_offset: int | None = None,
    end_offset: int | None = None,
    cfg: Settings = settings,
) -> dict[str, Any]:
    doc = get_document(document_id, cfg=cfg)
    tabs = {tab["tab_id"]: tab for tab in doc["document"]["tabs"]}
    if tab_id not in tabs:
        raise DocumentError("ERROR_DOCUMENT_COMMENT_ANCHOR", "Comment tab does not exist.")
    block = next((row for row in tabs[tab_id]["blocks"] if row["block_id"] == block_id), None)
    if block is None:
        raise DocumentError("ERROR_DOCUMENT_COMMENT_ANCHOR", "Comment block does not exist.")
    if not text.strip():
        raise DocumentError("ERROR_DOCUMENT_COMMENT_EMPTY", "Comment text is required.")
    if (start_offset is None) != (end_offset is None):
        raise DocumentError("ERROR_DOCUMENT_COMMENT_RANGE", "Both start and end offsets are required for a range comment.")
    if start_offset is not None and (start_offset < 0 or end_offset < start_offset):
        raise DocumentError("ERROR_DOCUMENT_COMMENT_RANGE", "Invalid comment range.")
    comment_id = _new_id("cmt")
    now = _now()
    payload = {
        "comment_id": comment_id,
        "document_id": document_id,
        "tab_id": tab_id,
        "block_id": block_id,
        "start_offset": start_offset,
        "end_offset": end_offset,
        "text": text.strip(),
        "state": "ACTIVE",
        "created_at": now,
        "updated_at": now,
    }
    with get_connection(cfg.sqlite_path) as conn:
        conn.execute(
            "INSERT INTO document_comments (comment_id, document_id, tab_id, block_id, start_offset, end_offset, state, created_at, updated_at, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (comment_id, document_id, tab_id, block_id, start_offset, end_offset, "ACTIVE", now, now, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
        )
    return payload


def list_comments(document_id: str, *, cfg: Settings = settings) -> list[dict[str, Any]]:
    _checked_id(document_id, "document_id")
    with get_connection(cfg.sqlite_path) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM document_comments WHERE document_id = ? ORDER BY created_at ASC, comment_id ASC",
            (document_id,),
        ).fetchall()
    return [json.loads(row["payload_json"]) for row in rows]


def set_comment_state(comment_id: str, state: str, *, cfg: Settings = settings) -> dict[str, Any]:
    _checked_id(comment_id, "comment_id")
    if state not in {"ACTIVE", "RESOLVED", "ORPHANED"}:
        raise DocumentError("ERROR_DOCUMENT_COMMENT_STATE", "Unsupported comment state.")
    now = _now()
    with get_connection(cfg.sqlite_path) as conn:
        row = conn.execute("SELECT payload_json FROM document_comments WHERE comment_id = ?", (comment_id,)).fetchone()
        if not row:
            raise DocumentError("ERROR_DOCUMENT_COMMENT_NOT_FOUND", "Comment not found.")
        payload = json.loads(row["payload_json"])
        payload["state"] = state
        payload["updated_at"] = now
        conn.execute(
            "UPDATE document_comments SET state = ?, updated_at = ?, payload_json = ? WHERE comment_id = ?",
            (state, now, json.dumps(payload, ensure_ascii=False, sort_keys=True), comment_id),
        )
    return payload


def store_asset(
    document_id: str,
    *,
    filename: str,
    media_type: str,
    content: bytes,
    cfg: Settings = settings,
) -> dict[str, Any]:
    get_document(document_id, cfg=cfg)
    if media_type not in ALLOWED_ASSET_TYPES:
        raise DocumentError("ERROR_DOCUMENT_ASSET_TYPE", f"Unsupported document asset type {media_type!r}.")
    if not content:
        raise DocumentError("ERROR_DOCUMENT_ASSET_EMPTY", "Document asset is empty.")
    if len(content) > MAX_ASSET_BYTES:
        raise DocumentError("ERROR_DOCUMENT_ASSET_TOO_LARGE", f"Document asset exceeds {MAX_ASSET_BYTES} bytes.")
    digest = sha256(content).hexdigest()
    safe_name = str(filename or "image").replace("\\", "/").split("/")[-1][:240] or "image"
    now = _now()
    with get_connection(cfg.sqlite_path) as conn:
        existing = conn.execute(
            "SELECT asset_id, filename, media_type, size_bytes, sha256, created_at FROM document_assets WHERE document_id = ? AND sha256 = ?",
            (document_id, digest),
        ).fetchone()
        if existing:
            row = dict(existing)
            return {**row, "document_id": document_id, "url": f"/document-assets/{row['asset_id']}"}
        asset_id = _new_id("asset")
        conn.execute(
            "INSERT INTO document_assets (asset_id, document_id, sha256, media_type, filename, size_bytes, created_at, content) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (asset_id, document_id, digest, media_type, safe_name, len(content), now, content),
        )
    return {
        "asset_id": asset_id, "document_id": document_id, "sha256": digest,
        "media_type": media_type, "filename": safe_name, "size_bytes": len(content),
        "created_at": now, "url": f"/document-assets/{asset_id}",
    }


def get_asset(asset_id: str, *, cfg: Settings = settings) -> dict[str, Any]:
    _checked_id(asset_id, "asset_id")
    with get_connection(cfg.sqlite_path) as conn:
        row = conn.execute(
            "SELECT asset_id, document_id, sha256, media_type, filename, size_bytes, created_at, content FROM document_assets WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()
    if not row:
        raise DocumentError("ERROR_DOCUMENT_ASSET_NOT_FOUND", "Document asset not found.")
    return dict(row)


def list_assets(document_id: str, *, cfg: Settings = settings) -> list[dict[str, Any]]:
    get_document(document_id, cfg=cfg)
    with get_connection(cfg.sqlite_path) as conn:
        rows = conn.execute(
            "SELECT asset_id, document_id, sha256, media_type, filename, size_bytes, created_at FROM document_assets WHERE document_id = ? ORDER BY created_at ASC, asset_id ASC",
            (document_id,),
        ).fetchall()
    return [{**dict(row), "url": f"/document-assets/{row['asset_id']}"} for row in rows]
