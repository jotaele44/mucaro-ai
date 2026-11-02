"""Negative and positive regression fixtures for seven-lock source integrity."""
from __future__ import annotations

from hashlib import sha256
import json

import pytest

from scripts.verify_security_lock_candidate import (
    LockEvidenceError, PATHS, PYPDF_SDIST_SHA256, PYPDF_WHEEL_SHA256, verify,
)

HEAD = "a" * 40


def fixture_root(tmp_path):
    rows = []
    for name in PATHS:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        body = ("pypdf==6.21.4 \\\n"
                f"    --hash=sha256:{PYPDF_WHEEL_SHA256} \\\n"
                f"    --hash=sha256:{PYPDF_SDIST_SHA256}\n").encode("utf-8")
        path.write_bytes(body)
        rows.append({"path": name, "bytes": len(body), "sha256": sha256(body).hexdigest()})
    (tmp_path / "lock-rebuild-manifest.json").write_text(json.dumps({
        "scope": "candidate-generated-only", "source_head": HEAD,
        "certification": "WITHHELD", "lock_count": len(PATHS), "locks": rows
    }), encoding="utf-8")
    return tmp_path


def test_seven_lock_candidate_integrity_pass(tmp_path):
    assert verify(fixture_root(tmp_path), HEAD)["lock_count"] == 7


def test_missing_member_blocks(tmp_path):
    root = fixture_root(tmp_path)
    (root / PATHS[0]).unlink()
    with pytest.raises(LockEvidenceError, match="missing"):
        verify(root, HEAD)


def test_modified_raw_bytes_block_even_if_pin_same(tmp_path):
    root = fixture_root(tmp_path)
    with (root / PATHS[2]).open("ab") as out:
        out.write(b"# silently edited\n")
    with pytest.raises(LockEvidenceError, match="byte count mismatch"):
        verify(root, HEAD)


def test_duplicate_member_manifest_blocks(tmp_path):
    root = fixture_root(tmp_path)
    path = root / "lock-rebuild-manifest.json"
    manifest = json.loads(path.read_text())
    manifest["locks"][0]["path"] = manifest["locks"][1]["path"]
    path.write_text(json.dumps(manifest))
    with pytest.raises(LockEvidenceError, match="duplicate"):
        verify(root, HEAD)


def test_source_head_mismatch_blocks(tmp_path):
    root = fixture_root(tmp_path)
    with pytest.raises(LockEvidenceError, match="source-head mismatch"):
        verify(root, "b" * 40)


def test_candidate_cannot_self_certify(tmp_path):
    root = fixture_root(tmp_path)
    path = root / "lock-rebuild-manifest.json"
    manifest = json.loads(path.read_text())
    manifest["certification"] = "PASS"
    path.write_text(json.dumps(manifest))
    with pytest.raises(LockEvidenceError, match="WITHHELD"):
        verify(root, HEAD)


def test_no_old_pypdf_pin_allowed(tmp_path):
    root = fixture_root(tmp_path)
    path = root / PATHS[1]
    raw = path.read_bytes().replace(b"6.21.4", b"6.21.2")
    path.write_bytes(raw)
    manifest_path = root / "lock-rebuild-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    row = next(x for x in manifest["locks"] if x["path"] == PATHS[1])
    row["bytes"] = len(raw)
    row["sha256"] = sha256(raw).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(LockEvidenceError, match="invalid pypdf pin"):
        verify(root, HEAD)


@pytest.mark.parametrize("bad_path", [[], {"unexpected": "object"}, None, 12])
def test_unhashable_or_nonstring_path_fails_closed(tmp_path, bad_path):
    root = fixture_root(tmp_path)
    manifest_path = root / "lock-rebuild-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["locks"][0]["path"] = bad_path
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(LockEvidenceError):
        verify(root, HEAD)


def test_unexpected_manifest_row_field_fails_closed(tmp_path):
    root = fixture_root(tmp_path)
    manifest_path = root / "lock-rebuild-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["locks"][0]["unexpected"] = "ignore-me"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(LockEvidenceError, match="malformed"):
        verify(root, HEAD)


def test_source_file_symlink_fails_closed(tmp_path):
    root = fixture_root(tmp_path)
    link = root / PATHS[0]
    original = root / PATHS[1]
    link.unlink()
    link.symlink_to(original)
    with pytest.raises(LockEvidenceError, match="linked"):
        verify(root, HEAD)
