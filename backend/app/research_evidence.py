"""Read-only replay of frozen web snapshots; integrity is not truth or identity."""

from __future__ import annotations

import base64
import binascii
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from .artifact_identity import sha256_bytes

MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
MAX_PACKET_CONTENT_BYTES = 128 * 1024 * 1024
HEX = re.compile(r"[0-9a-f]{64}")


def _digest(value: object) -> str:
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ValueError("invalid SHA-256")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def read_json_strict(raw: bytes) -> object:
    return json.loads(
        raw,
        object_pairs_hook=_unique_object,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")),
    )


def verify_snapshot(root: Path, entry: dict) -> tuple[dict, bytes]:
    """Expected snapshot hash must originate in the frozen acquisition manifest."""
    relative = Path(entry["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("snapshot path escapes evidence root")
    root = root.resolve(strict=True)
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("snapshot path escapes evidence root or is not a file")
    with path.open("rb") as handle:
        raw = handle.read(MAX_SNAPSHOT_BYTES + 1)
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot exceeds replay byte limit")
    if sha256_bytes(raw) != _digest(entry["snapshot_sha256"]):
        raise ValueError("snapshot byte mismatch")
    snapshot = read_json_strict(raw)
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != "mucaro_web_snapshot_v1":
        raise ValueError("unsupported snapshot schema")
    for field in ("snapshot_id", "requested_url", "final_url", "retrieval_utc"):
        if not isinstance(snapshot.get(field), str) or not snapshot[field].strip():
            raise ValueError(f"missing {field}")
    when = datetime.fromisoformat(snapshot["retrieval_utc"])
    if when.tzinfo is None or when.utcoffset() != timedelta(0):
        raise ValueError("retrieval time must be UTC")
    if type(snapshot.get("http_status")) is not int or not 100 <= snapshot["http_status"] <= 599:
        raise ValueError("invalid HTTP status")
    try:
        content = base64.b64decode(snapshot["content_base64"], validate=True)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ValueError("invalid base64 content") from exc
    if sha256_bytes(content) != _digest(snapshot["content_sha256"]):
        raise ValueError("content byte mismatch")
    if type(snapshot.get("content_size_bytes")) is not int or len(content) != snapshot["content_size_bytes"]:
        raise ValueError("content length mismatch")
    return snapshot, content


def audit_packet(root: Path, packet: dict) -> dict:
    """Fail closed, retaining per-item failures and exact declared denominators.

    Claim spans use byte offsets in raw HTTP content, never normalized/OCR text.
    Span integrity does not assess entailment. Error responses can be preserved
    but cannot serve as affirmative evidence in this first bounded adapter.
    """
    if not isinstance(packet, dict) or packet.get("schema_version") != "mucaro-research-packet/1":
        raise ValueError("unsupported research packet schema")
    sources, claims = packet.get("sources"), packet.get("claims")
    if not isinstance(sources, list) or not sources or not isinstance(claims, list) or not claims:
        raise ValueError("nonempty sources and claims required")
    source_rows, claim_rows, verified, seen = [], [], {}, set()
    duplicate_sources = set()
    retained_bytes = 0
    for entry in sources:
        source_id = entry.get("source_id") if isinstance(entry, dict) else None
        try:
            if not isinstance(source_id, str) or not source_id.strip():
                raise ValueError("missing source ID")
            if source_id in seen:
                duplicate_sources.add(source_id)
                verified.pop(source_id, None)
                raise ValueError("duplicate source ID")
            seen.add(source_id)
            snapshot, content = verify_snapshot(root, entry)
            if retained_bytes + len(content) > MAX_PACKET_CONTENT_BYTES:
                raise ValueError("packet content memory budget exceeded")
            retained_bytes += len(content)
            verified[source_id] = snapshot, content
            source_rows.append(
                {
                    "source_id": source_id,
                    "status": "PASS",
                    "snapshot_sha256": entry["snapshot_sha256"],
                    "content_sha256": snapshot["content_sha256"],
                    "snapshot_id": snapshot["snapshot_id"],
                    "retrieval_utc": snapshot["retrieval_utc"],
                    "content_size_bytes": len(content),
                }
            )
        except (ValueError, KeyError, TypeError, OSError) as exc:
            source_rows.append({"source_id": source_id, "status": "FAIL", "reason": str(exc)})
    claim_ids = set()
    for claim in claims:
        claim_id = claim.get("claim_id") if isinstance(claim, dict) else None
        try:
            if not isinstance(claim_id, str) or not claim_id.strip() or claim_id in claim_ids:
                raise ValueError("missing or duplicate claim ID")
            claim_ids.add(claim_id)
            if not isinstance(claim.get("text"), str) or not claim["text"].strip():
                raise ValueError("claim text required")
            source_id = claim["source_id"]
            if not isinstance(source_id, str) or source_id in duplicate_sources or source_id not in verified:
                raise ValueError("source unavailable or ambiguous")
            snapshot, content = verified[source_id]
            if not 200 <= snapshot["http_status"] < 300:
                raise ValueError("HTTP response not eligible for affirmative evidence")
            start, end = claim["byte_start"], claim["byte_end"]
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(content):
                raise ValueError("invalid raw byte span")
            if sha256_bytes(content[start:end]) != _digest(claim["span_sha256"]):
                raise ValueError("claim span byte mismatch")
            claim_rows.append({"claim_id": claim_id, "status": "PASS", "entailment": "OPEN"})
        except (ValueError, KeyError, TypeError) as exc:
            claim_rows.append({"claim_id": claim_id, "status": "FAIL", "reason": str(exc)})
    failed = sum(row["status"] == "FAIL" for row in source_rows + claim_rows)
    return {
        "schema_version": "mucaro-research-audit/1",
        "integrity_status": "FAIL" if failed else "PASS",
        "sources_declared": len(sources),
        "sources_processed": len(source_rows),
        "claims_declared": len(claims),
        "claims_processed": len(claim_rows),
        "failures": failed,
        "sources": source_rows,
        "claims": claim_rows,
        "evidence_truth": "OPEN",
        "identity_adjudication": "OPEN",
        "independent_verification": "OPEN",
        "release_certification": "WITHHELD",
    }
