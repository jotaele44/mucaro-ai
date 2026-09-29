from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from backend.app.portable_runtime import (
    PortableRuntimeError,
    build_portable_macos_app,
    verify_portable_macos_app,
)


def git(root: Path, *args: str) -> None:
    proc = subprocess.run(["git", *args], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    assert proc.returncode == 0, proc.stderr


def fixture_project(root: Path) -> tuple[Path, Path]:
    (root / "backend").mkdir()
    (root / "backend" / "marker.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "launch_app.py").write_text("print('launch')\n", encoding="utf-8")
    (root / "frontend" / "dist").mkdir(parents=True)
    (root / "frontend" / "dist" / "index.html").write_text("<html>Mucaro</html>\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nname = "mucaro-ai"\nversion = "0.1.23"\n', encoding="utf-8")
    git(root, "init")
    git(root, "config", "user.email", "mucaro-tests@example.invalid")
    git(root, "config", "user.name", "Mucaro Tests")
    git(root, "add", "backend/marker.py", "scripts/launch_app.py", "pyproject.toml")
    git(root, "commit", "-m", "fixture")

    runtime = root / "runtime-input"
    (runtime / "bin").mkdir(parents=True)
    py = runtime / "bin" / "python3"
    py.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    py.chmod(py.stat().st_mode | 0o755)
    (runtime / "lib").mkdir()
    (runtime / "lib" / "runtime.txt").write_text("runtime\n", encoding="utf-8")
    return root, runtime


def test_portable_bundle_is_bundle_relative_and_hash_verified(tmp_path: Path) -> None:
    root, runtime = fixture_project(tmp_path)
    report = build_portable_macos_app(root, runtime, output_dir=root / "out")
    assert report["status"] == "PORTABLE_APP_VERIFIED"
    assert all(row["status"] == "PASS" for row in report["checks"])
    state = report["distribution_state"]
    assert state["embedded_runtime"] is True
    assert state["bundle_relative_launch"] is True
    assert state["signed"] is False
    assert state["clean_machine_executed"] is False

    app = Path(report["app_path"])
    launcher = (app / "Contents" / "MacOS" / "mucaro-ai").read_text(encoding="utf-8")
    assert str(root) not in launcher
    assert "${RESOURCES}/runtime/bin/python3" in launcher
    assert "PYTHONNOUSERSITE=1" in launcher
    assert "PYTHONSAFEPATH=1" in launcher
    assert "MUCARO_AI_DATA_DIR" in launcher
    assert "/Library/Application Support/Mucaro AI" in launcher
    assert '"${PYTHON_EXECUTABLE}" -E -s -P -B' in launcher
    assert verify_portable_macos_app(app)["status"] == "PORTABLE_APP_VERIFIED"


def test_runtime_symlink_fails_closed(tmp_path: Path) -> None:
    root, runtime = fixture_project(tmp_path)
    target = runtime / "lib" / "runtime.txt"
    link = runtime / "lib" / "linked.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(PortableRuntimeError) as exc:
        build_portable_macos_app(root, runtime, output_dir=root / "out")
    assert exc.value.status == "ERROR_PORTABLE_RUNTIME_SYMLINK"


def test_runtime_python_must_be_executable(tmp_path: Path) -> None:
    root, runtime = fixture_project(tmp_path)
    py = runtime / "bin" / "python3"
    py.chmod(0o644)
    with pytest.raises(PortableRuntimeError) as exc:
        build_portable_macos_app(root, runtime, output_dir=root / "out")
    assert exc.value.status == "ERROR_PORTABLE_RUNTIME_PYTHON"


def test_portable_bundle_rejects_tracked_worktree_changes(tmp_path: Path) -> None:
    root, runtime = fixture_project(tmp_path)
    (root / "backend" / "marker.py").write_text("VALUE = 2\n", encoding="utf-8")

    with pytest.raises(PortableRuntimeError) as exc:
        build_portable_macos_app(root, runtime, output_dir=root / "out")

    assert exc.value.status == "ERROR_PORTABLE_WORKTREE_DIRTY"


def test_portable_bundle_binds_runtime_provenance_receipt(tmp_path: Path) -> None:
    root, runtime = fixture_project(tmp_path)
    receipt = runtime / "runtime-provenance.json"
    receipt.write_text('{"status":"PORTABLE_RUNTIME_SOURCE_READY"}\n', encoding="utf-8")
    receipt_sha256 = hashlib.sha256(receipt.read_bytes()).hexdigest()

    report = build_portable_macos_app(
        root,
        runtime,
        output_dir=root / "out",
        runtime_provenance={
            "receipt_sha256": receipt_sha256,
            "status": "PORTABLE_RUNTIME_VERIFIED",
        },
    )
    assert report["status"] == "PORTABLE_APP_VERIFIED"

    embedded_receipt = Path(report["app_path"]) / "Contents/Resources/runtime/runtime-provenance.json"
    embedded_receipt.write_text('{"status":"TAMPERED"}\n', encoding="utf-8")
    tampered = verify_portable_macos_app(Path(report["app_path"]))
    assert tampered["status"] == "PORTABLE_APP_VERIFY_FAILED"
    assert "runtime_provenance_receipt" in tampered["failed_checks"]


def test_portable_manifest_version_must_match_bundled_metadata(tmp_path: Path) -> None:
    root, runtime = fixture_project(tmp_path)
    report = build_portable_macos_app(root, runtime, output_dir=root / "out")
    app = Path(report["app_path"])
    manifest = app / "Contents/Resources/portable-manifest.json"
    import json
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["release_version"] = "9.9.9"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    result = verify_portable_macos_app(app)
    assert result["status"] == "PORTABLE_APP_VERIFY_FAILED"
    assert "release_version_matches_package" in result["failed_checks"]
