#!/usr/bin/env python3
"""Strict, side-effect-free verification of a candidate seven-lock bundle.

This checks source lineage and raw byte conservation only. Successful output
does not prove dependency safety, lock reproducibility, or release eligibility.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re

PATHS = (
    "packaging/one_click/requirements-aarch64-apple-darwin.lock",
    "packaging/one_click/requirements-x86_64-apple-darwin.lock",
    "packaging/one_click/requirements-x86_64-unknown-linux-gnu.lock",
    "packaging/one_click/requirements-x86_64-pc-windows-msvc.lock",
    "packaging/runtime/requirements-macos-x86_64.lock",
    "packaging/runtime/requirements-macos-arm64.lock",
    "packaging/macos/requirements-macos-arm64.lock",
)
PYPDF_WHEEL_SHA256 = "33e4fb41ca352810eb4e9838aa0d82d6888a5f8bf682f43e99cce5faa666255c"
PYPDF_SDIST_SHA256 = "c8817e3f888d47a087872ded69812c1ef30cacb871d6e053e0c00bbaf0bc2163"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
HEAD_RE = re.compile(r"^[0-9a-f]{40}$")


class LockEvidenceError(ValueError):
    pass


def verify(root: Path, expected_head: str) -> dict:
    if not HEAD_RE.fullmatch(expected_head):
        raise LockEvidenceError("invalid expected 40-character source SHA")
    base = root.resolve()
    manifest_path = base / "lock-rebuild-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LockEvidenceError("missing or invalid lock manifest") from exc
    if not isinstance(manifest, dict) or manifest.get("scope") != "candidate-generated-only":
        raise LockEvidenceError("unexpected manifestation scope")
    if manifest.get("source_head") != expected_head:
        raise LockEvidenceError("source-head mismatch")
    if manifest.get("certification") != "WITHHELD":
        raise LockEvidenceError("candidate manifest must remain WITHHELD")
    if manifest.get("lock_count") != len(PATHS) or not isinstance(manifest.get("locks"), list):
        raise LockEvidenceError("seven-lock denominator mismatch")
    rows = manifest["locks"]
    paths = [r.get("path") for r in rows if isinstance(r, dict)]
    if len(rows) != len(paths) or len(paths) != len(set(paths)) or set(paths) != set(PATHS):
        raise LockEvidenceError("missing, duplicate, or unexpected lock paths")
    for row in rows:
        name = row["path"]
        file_path = base.joinpath(*name.split("/"))
        if file_path.is_symlink() or not file_path.is_file() or not file_path.resolve().is_relative_to(base):
            raise LockEvidenceError("missing, unsafe or linked lock file: " + name)
        raw = file_path.read_bytes()
        if not isinstance(row.get("bytes"), int) or isinstance(row["bytes"], bool) or len(raw) != row["bytes"]:
            raise LockEvidenceError("lock byte count mismatch: " + name)
        digest = row.get("sha256")
        if not isinstance(digest, str) or not SHA_RE.fullmatch(digest) or sha256(raw).hexdigest() != digest:
            raise LockEvidenceError("lock SHA-256 mismatch: " + name)
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LockEvidenceError("non-UTF8 lock file: " + name) from exc
        if "pypdf==6.21.2" in content or len(re.findall(r"(?m)^pypdf==6\.21\.4\s", content)) != 1:
            raise LockEvidenceError("invalid pypdf pin: " + name)
        stanza = content.split("pypdf==6.21.4", 1)[1].split("\n", 4)[:3]
        hashes = "\n".join(stanza)
        for digest in (PYPDF_WHEEL_SHA256, PYPDF_SDIST_SHA256):
            if "--hash=sha256:" + digest not in hashes:
                raise LockEvidenceError("missing published pypdf hash: " + name)
    return {"status": "PASS", "source_head": expected_head, "lock_count": len(PATHS),
            "scope": "candidate-integrity-only", "certification": "WITHHELD"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--source-head", required=True)
    args = parser.parse_args()
    try:
        result = verify(args.root, args.source_head)
    except LockEvidenceError as exc:
        print(json.dumps({"status": "FAIL", "reason": str(exc), "certification": "WITHHELD"}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
