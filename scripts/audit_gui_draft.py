#!/usr/bin/env python3
"""Offline, source-bounded GUI draft archive audit; no network or dependencies."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import zipfile

EXPECTED_GUI_ZIP_SHA256 = "20047e9421b6eeb9329a2e6f39a9da7d39f5bf2ab39303815cceb41692851bd6"
EXPECTED_OWL_SHA256 = "783e24be90f11ed800199cc0adb1d45f4c37ba2dc4cc8175864ebd521fdbec16"
EXPECTED_PAYLOAD_MANIFEST_SHA256 = "bd10e542bdfba30fd0099201d929d7e895e1d81b2937dde625d50d6382337231"
MAX_ENTRIES = 10000
MAX_UNCOMPRESSED = 1 << 30


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def audit_archive(path: Path) -> dict:
    outer = digest(path.read_bytes())
    members = []
    unique = set()
    portable_unique = set()
    total = 0
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ENTRIES:
            raise ValueError("ENTRY_LIMIT_EXCEEDED")
        if sum(e.file_size for e in entries if not e.is_dir()) > MAX_UNCOMPRESSED:
            raise ValueError("UNCOMPRESSED_SIZE_LIMIT")
        for entry in entries:
            if entry.is_dir():
                continue
            raw = entry.filename
            portable = raw.replace("\\", "/")
            parts = PurePosixPath(portable)
            if (not raw or parts.is_absolute() or ".." in parts.parts
                or re.match(r"^[A-Za-z]:", portable) or "\x00" in raw):
                raise ValueError("UNSAFE_PATH: " + repr(raw))
            if raw in unique:
                raise ValueError("DUPLICATE_MEMBER_PATH: " + repr(raw))
            unique.add(raw)
            if portable in portable_unique:
                raise ValueError("CROSS_PLATFORM_MEMBER_PATH_COLLISION: " + repr(raw))
            portable_unique.add(portable)
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("SYMLINK_MEMBER: " + repr(raw))
            if entry.flag_bits & 1:
                raise ValueError("ENCRYPTED_MEMBER: " + repr(raw))
            total += entry.file_size
            if total > MAX_UNCOMPRESSED:
                raise ValueError("UNCOMPRESSED_SIZE_LIMIT")
            blob = archive.read(entry)
            if len(blob) != entry.file_size:
                raise ValueError("UNCOMPRESSED_SIZE_MISMATCH: " + repr(raw))
            members.append({
                "path": raw,
                "uncompressed_bytes": entry.file_size,
                "sha256": digest(blob),
                "compression_method": entry.compress_type,
                "compressed_bytes": entry.compress_size,
                "date_time": list(entry.date_time),
                "external_attr": entry.external_attr,
                "flag_bits": entry.flag_bits,
                "comment_hex": entry.comment.hex(),
            })
        if archive.testzip() is not None:
            raise ValueError("CRC_FAILED")
        order = [e.filename for e in entries if not e.is_dir()]
        archive_comment = archive.comment.hex()
    members.sort(key=lambda m: m["path"])
    canonical = "".join(f'{m["path"]}\t{m["uncompressed_bytes"]}\t{m["sha256"]}\n' for m in members).encode("utf-8")
    return {
        "archive_sha256": outer, "member_count": len(members),
        "archive_entry_count": len(entries), "total_uncompressed_bytes": total,
        "payload_manifest_sha256": digest(canonical),
        "member_order": order, "archive_comment_hex": archive_comment,
        "members": members,
    }


def compare(a: dict, b: dict) -> dict:
    amap = {(m["path"], m["uncompressed_bytes"], m["sha256"]) for m in a["members"]}
    bmap = {(m["path"], m["uncompressed_bytes"], m["sha256"]) for m in b["members"]}
    apayload = Counter((m["uncompressed_bytes"], m["sha256"]) for m in a["members"])
    bpayload = Counter((m["uncompressed_bytes"], m["sha256"]) for m in b["members"])
    if a["archive_sha256"] == b["archive_sha256"]:
        state = "BYTE_IDENTICAL"
    elif amap == bmap:
        ma, mb = {m["path"]:m for m in a["members"]}, {m["path"]:m for m in b["members"]}
        provenance_fields = ("date_time", "external_attr", "flag_bits", "comment_hex")
        metadata_equal = (a["member_order"] == b["member_order"]
                          and a["archive_comment_hex"] == b["archive_comment_hex"]
                          and all(all(ma[k][p] == mb[k][p] for p in provenance_fields) for k in ma))
        compression_changed = any((ma[k]["compression_method"], ma[k]["compressed_bytes"])
                                  != (mb[k]["compression_method"], mb[k]["compressed_bytes"]) for k in ma)
        state = "PURE_RECOMPRESSION" if metadata_equal and compression_changed else "UNRESOLVED"
    elif apayload == bpayload:
        state = "SAME_PAYLOADS_DIFFERENT_PATHS"
    else:
        state = "DISTINCT_PAYLOADS"
    return {
        "classification": state,
        "intersection": len(amap & bmap),
        "a_only": len(amap - bmap),
        "b_only": len(bmap - amap),
        "union": len(amap | bmap),
        "symmetric_difference": len(amap ^ bmap),
        "payload_multisets_equal": apayload == bpayload,
        "scope": "exact archive member path + uncompressed size + SHA256; container metadata inspected separately",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--owl", type=Path, help="Optional separate original owl image")
    parser.add_argument("--verify-known", action="store_true", help="Fail unless original uploaded sources match frozen input")
    parser.add_argument("--compare", type=Path, help="Optional second archive for source-bounded equivalence")
    parser.add_argument("--output", type=Path, help="Write current run receipt to a new path (never silently overwrite)")
    args = parser.parse_args(argv)
    try:
        a = audit_archive(args.archive)
        owl = digest(args.owl.read_bytes()) if args.owl else None
        checks = {
            "zip_byte_sha256": a["archive_sha256"] == EXPECTED_GUI_ZIP_SHA256,
            "member_payload_manifest_sha256": a["payload_manifest_sha256"] == EXPECTED_PAYLOAD_MANIFEST_SHA256,
            "owl_byte_sha256": owl == EXPECTED_OWL_SHA256 if args.owl else None,
        }
        passed = all(value is True for value in checks.values() if value is not None)
        if args.verify_known and (not passed or owl is None):
            raise ValueError("FROZEN_INPUT_MISMATCH: " + str(checks))
        receipt = {
            "schema_version": "mucaro-gui-intake/1.0",
            "run_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "scope": "uploaded GUI ZIP and original owl image only; not application or release certification",
            "state": "PASS" if passed else "PROVISIONAL",
            "checks": checks, "archive": a,
            "owl_sha256": owl,
        }
        if args.compare:
            receipt["comparison"] = compare(a, audit_archive(args.compare))
        encoded = json.dumps(receipt, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            if args.output.exists():
                raise ValueError("REFUSE_OVERWRITE_EXISTING_RECEIPT")
            args.output.write_text(encoded, encoding="utf-8")
        print(encoded)
        return 0
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        print("FAIL: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
