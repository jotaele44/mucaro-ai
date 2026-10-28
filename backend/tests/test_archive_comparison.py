from __future__ import annotations

import zipfile
from pathlib import Path

from backend.app.packaging import compare_archives


def _write_zip(path: Path, members: dict[str, bytes], *, compression: int = zipfile.ZIP_DEFLATED) -> None:
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)


def test_archive_comparison_classifies_byte_identity(tmp_path: Path) -> None:
    left = tmp_path / "left.zip"
    right = tmp_path / "right.zip"
    _write_zip(left, {"a.txt": b"same"})
    right.write_bytes(left.read_bytes())

    result = compare_archives(left, right)

    assert result["classification"] == "BYTE_IDENTICAL"
    assert result["payload_multiset_equal"] is True


def test_archive_comparison_distinguishes_recompression_and_path_changes(tmp_path: Path) -> None:
    left = tmp_path / "left.zip"
    recompressed = tmp_path / "recompressed.zip"
    relocated = tmp_path / "relocated.zip"
    _write_zip(left, {"a.txt": b"same"})
    _write_zip(recompressed, {"a.txt": b"same"}, compression=zipfile.ZIP_STORED)
    _write_zip(relocated, {"renamed.txt": b"same"}, compression=zipfile.ZIP_STORED)

    assert compare_archives(left, recompressed)["classification"] == "PURE_RECOMPRESSION"
    relocated_result = compare_archives(left, relocated)
    assert relocated_result["classification"] == "SAME_PAYLOADS_DIFFERENT_PATHS"
    assert relocated_result["path_aware_members_equal"] is False


def test_archive_comparison_reports_distinct_payloads(tmp_path: Path) -> None:
    left = tmp_path / "left.zip"
    right = tmp_path / "right.zip"
    _write_zip(left, {"a.txt": b"same"})
    _write_zip(right, {"a.txt": b"changed"})

    result = compare_archives(left, right)

    assert result["classification"] == "DISTINCT_PAYLOADS"
    assert result["payload_multiset_equal"] is False
