from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from backend.app.portable_runtime_source import (
    PortableRuntimeSourceError,
    _isolated_python_env,
    _parse_lock,
    _validate_archive_bytes,
    extract_runtime_archive,
    inspect_runtime_archive,
    load_runtime_source_manifest,
)


def _archive(path: Path, entries: list[tuple[str, bytes | None, str | None]]) -> dict[str, object]:
    regular_bytes = 0
    regular_count = 0
    symlink_count = 0
    with tarfile.open(path, "w:gz") as archive:
        for name, content, linkname in entries:
            member = tarfile.TarInfo(name)
            if linkname is not None:
                member.type = tarfile.SYMTYPE
                member.linkname = linkname
                symlink_count += 1
                archive.addfile(member)
            else:
                assert content is not None
                member.size = len(content)
                member.mode = 0o755 if name.endswith("python3") else 0o644
                regular_bytes += len(content)
                regular_count += 1
                archive.addfile(member, io.BytesIO(content))
    return {
        "target": {"archive_root": "python"},
        "archive_policy": {
            "member_count": len(entries),
            "regular_file_count": regular_count,
            "symlink_count": symlink_count,
            "total_regular_bytes": regular_bytes,
            "max_members": 10,
            "max_regular_bytes": 1024,
        },
    }


def test_archive_inspector_freezes_logical_and_payload_identity(tmp_path: Path) -> None:
    archive = tmp_path / "runtime.tar.gz"
    _archive(
        archive,
        [
            ("python/bin/python3", b"#!/bin/sh\nexit 0\n", None),
            ("python/lib/example.dat", b"payload", None),
            ("python/bin/python", None, "python3"),
        ],
    )

    report = inspect_runtime_archive(archive)

    assert report["archive_root"] == "python"
    assert report["archive_bytes"] == archive.stat().st_size
    assert report["archive_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert report["member_count"] == 3
    assert report["regular_file_count"] == 2
    assert report["symlink_count"] == 1
    assert report["total_regular_bytes"] == 24
    assert len(report["logical_members_sha256"]) == 64
    assert len(report["payload_multiset_sha256"]) == 64
    assert report["logical_members_sha256"] != report["payload_multiset_sha256"]


def test_archive_inspector_rejects_multiple_roots(tmp_path: Path) -> None:
    archive = tmp_path / "runtime.tar.gz"
    _archive(
        archive,
        [
            ("python/bin/python3", b"python", None),
            ("other/file", b"other", None),
        ],
    )

    with pytest.raises(PortableRuntimeSourceError) as exc:
        inspect_runtime_archive(archive)

    assert exc.value.status == "ERROR_PORTABLE_RUNTIME_ARCHIVE_ROOT"


def test_archive_inspector_rejects_broken_symlink(tmp_path: Path) -> None:
    archive = tmp_path / "runtime.tar.gz"
    _archive(
        archive,
        [
            ("python/bin/python3", b"python", None),
            ("python/bin/python", None, "missing"),
        ],
    )

    with pytest.raises(PortableRuntimeSourceError) as exc:
        inspect_runtime_archive(archive)

    assert exc.value.status == "ERROR_PORTABLE_RUNTIME_SYMLINK_TARGET"


def test_extract_rejects_frozen_logical_manifest_drift(tmp_path: Path) -> None:
    archive = tmp_path / "runtime.tar.gz"
    manifest = _archive(archive, [("python/bin/python3", b"python", None)])
    manifest["archive_policy"]["logical_members_sha256"] = "0" * 64

    with pytest.raises(PortableRuntimeSourceError) as exc:
        extract_runtime_archive(archive, tmp_path / "runtime", manifest)

    assert exc.value.status == "ERROR_PORTABLE_RUNTIME_ARCHIVE_IDENTITY"


def test_archive_symlink_is_contained_and_materialized(tmp_path: Path) -> None:
    archive = tmp_path / "runtime.tar.gz"
    manifest = _archive(
        archive,
        [
            ("python/bin/python3", b"#!/bin/sh\nexit 0\n", None),
            ("python/bin/python", None, "python3"),
        ],
    )

    counts = extract_runtime_archive(archive, tmp_path / "runtime", manifest)

    assert counts == {
        "member_count": 2,
        "regular_file_count": 1,
        "symlink_count": 1,
        "total_regular_bytes": 17,
    }
    assert (tmp_path / "runtime/bin/python").read_bytes() == b"#!/bin/sh\nexit 0\n"
    assert not (tmp_path / "runtime/bin/python").is_symlink()


def test_archive_path_traversal_fails_closed(tmp_path: Path) -> None:
    archive = tmp_path / "runtime.tar.gz"
    manifest = _archive(archive, [("python/../escape", b"bad", None)])

    with pytest.raises(PortableRuntimeSourceError) as exc:
        extract_runtime_archive(archive, tmp_path / "runtime", manifest)

    assert exc.value.status == "ERROR_PORTABLE_RUNTIME_ARCHIVE_PATH"
    assert not (tmp_path / "escape").exists()


def test_archive_symlink_escape_fails_closed(tmp_path: Path) -> None:
    archive = tmp_path / "runtime.tar.gz"
    manifest = _archive(
        archive,
        [
            ("python/bin/python3", b"python", None),
            ("python/bin/python", None, "../../outside"),
        ],
    )

    with pytest.raises(PortableRuntimeSourceError) as exc:
        extract_runtime_archive(archive, tmp_path / "runtime", manifest)

    assert exc.value.status == "ERROR_PORTABLE_RUNTIME_SYMLINK_TARGET"
    assert not (tmp_path / "runtime").exists()


def test_dependency_lock_requires_exact_version_and_hash(tmp_path: Path) -> None:
    valid = tmp_path / "valid.lock"
    valid.write_text("example==1.2.3 \\\n+    --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")
    assert _parse_lock(valid) == {"example": "1.2.3"}

    invalid = tmp_path / "invalid.lock"
    invalid.write_text("example==1.2.3\n", encoding="utf-8")
    with pytest.raises(PortableRuntimeSourceError) as exc:
        _parse_lock(invalid)
    assert exc.value.status == "ERROR_PORTABLE_RUNTIME_LOCK"


def test_runtime_environment_excludes_host_python_and_pip_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONPATH", "/untrusted/python")
    monkeypatch.setenv("PYTHONHOME", "/untrusted/home")
    monkeypatch.setenv("PIP_INDEX_URL", "https://untrusted.invalid/simple")

    env = _isolated_python_env()

    assert "PYTHONPATH" not in env
    assert "PYTHONHOME" not in env
    assert "PIP_INDEX_URL" not in env
    assert env["PYTHONNOUSERSITE"] == "1"
    assert env["PYTHONSAFEPATH"] == "1"


def test_runtime_lock_must_match_direct_project_dependencies(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="fixture"\nversion="1.0"\ndependencies=["example==1.0"]\n',
        encoding="utf-8",
    )
    lock = tmp_path / "runtime.lock"
    lock.write_text("example==2.0 \\\n+    --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")
    manifest = tmp_path / "runtime.json"
    manifest.write_text(
        """{
  "archive_policy": {
    "max_members": 1,
    "max_regular_bytes": 1,
    "member_count": 1,
    "regular_file_count": 1,
    "symlink_count": 0,
    "total_regular_bytes": 1
  },
  "dependency_lock": "runtime.lock",
  "required_imports": ["example"],
  "schema_version": "mucaro-portable-runtime-source/1.0",
  "source": {
    "bytes": 1,
    "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "url": "https://github.com/example/runtime"
  },
  "target": {}
}
""",
        encoding="utf-8",
    )

    with pytest.raises(PortableRuntimeSourceError) as exc:
        load_runtime_source_manifest(tmp_path, manifest)

    assert exc.value.status == "ERROR_PORTABLE_RUNTIME_LOCK_PROJECT_MISMATCH"


def _source_manifest(tmp_path: Path, url: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="fixture"\nversion="1.0"\ndependencies=["example==1.0"]\n',
        encoding="utf-8",
    )
    (tmp_path / "runtime.lock").write_text("example==1.0 \\\n+    --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")
    manifest = tmp_path / "runtime.json"
    manifest.write_text(
        json.dumps(
            {
                "archive_policy": {
                    "max_members": 1,
                    "max_regular_bytes": 1,
                    "member_count": 1,
                    "regular_file_count": 1,
                    "symlink_count": 0,
                    "total_regular_bytes": 1,
                },
                "dependency_lock": "runtime.lock",
                "required_imports": ["example"],
                "schema_version": "mucaro-portable-runtime-source/1.0",
                "source": {"bytes": 1, "sha256": "a" * 64, "url": url},
                "target": {},
            }
        ),
        encoding="utf-8",
    )
    return manifest


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/example/runtime",
        "https://github.com.attacker.invalid/example/runtime",
        "https://objects.githubusercontent.com/example/runtime",
        "file:///etc/passwd",
    ],
)
def test_runtime_source_url_must_be_https_github(tmp_path: Path, url: str) -> None:
    assert load_runtime_source_manifest(tmp_path, _source_manifest(tmp_path, "https://github.com/example/runtime"))

    with pytest.raises(PortableRuntimeSourceError) as exc:
        load_runtime_source_manifest(tmp_path, _source_manifest(tmp_path, url))
    assert exc.value.status == "ERROR_PORTABLE_RUNTIME_SOURCE_URL"


def test_downloaded_archive_must_match_pinned_size_and_sha256(tmp_path: Path) -> None:
    archive = tmp_path / "runtime.tar.gz"
    archive.write_bytes(b"x")
    pinned = {"bytes": 1, "sha256": hashlib.sha256(b"x").hexdigest()}
    _validate_archive_bytes(archive, pinned)

    archive.write_bytes(b"y")
    with pytest.raises(PortableRuntimeSourceError) as exc:
        _validate_archive_bytes(archive, pinned)
    assert exc.value.status == "ERROR_PORTABLE_RUNTIME_SOURCE_HASH"

    archive.write_bytes(b"xy")
    with pytest.raises(PortableRuntimeSourceError) as exc:
        _validate_archive_bytes(archive, pinned)
    assert exc.value.status == "ERROR_PORTABLE_RUNTIME_SOURCE_SIZE"
