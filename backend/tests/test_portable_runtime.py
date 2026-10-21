from __future__ import annotations

import hashlib
import json
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


def test_portable_bundle_leaves_out_tracked_app_bundles(tmp_path: Path) -> None:
    root, runtime = fixture_project(tmp_path)
    tracked_app = root / "Mucaro AI Setup.app" / "Contents"
    tracked_app.mkdir(parents=True)
    (tracked_app / "Info.plist").write_text("<plist/>\n", encoding="utf-8")
    git(root, "add", "Mucaro AI Setup.app/Contents/Info.plist")
    git(root, "commit", "-m", "add source app fixture")

    report = build_portable_macos_app(root, runtime, output_dir=root / "out")

    assert report["status"] == "PORTABLE_APP_VERIFIED"
    app = Path(report["app_path"])
    assert not (app / "Contents" / "Resources" / "app" / "Mucaro AI Setup.app").exists()
    manifest = json.loads((app / "Contents" / "Resources" / "portable-manifest.json").read_text(encoding="utf-8"))
    application = manifest["application"]
    assert application["excluded_tracked_app_bundle_count"] == 1
    assert application["excluded_tracked_app_bundles"] == ["Mucaro AI Setup.app"]
    assert all(".app/" not in row["path"].lower() for row in application["members"])
    checks = {row["name"]: row["status"] for row in report["checks"]}
    assert checks["application_excludes_nested_app_bundles"] == "PASS"
    assert checks["application_exclusion_receipt"] == "PASS"


def test_portable_bundle_excludes_case_insensitive_app_suffix(tmp_path: Path) -> None:
    root, runtime = fixture_project(tmp_path)
    tracked_app = root / "Legacy.APP" / "Contents"
    tracked_app.mkdir(parents=True)
    (tracked_app / "Info.plist").write_text("<plist/>\n", encoding="utf-8")
    git(root, "add", "Legacy.APP/Contents/Info.plist")
    git(root, "commit", "-m", "add uppercase app fixture")

    report = build_portable_macos_app(root, runtime, output_dir=root / "out")

    assert report["status"] == "PORTABLE_APP_VERIFIED"
    app = Path(report["app_path"])
    assert not (app / "Contents" / "Resources" / "app" / "Legacy.APP").exists()
    manifest = json.loads((app / "Contents" / "Resources" / "portable-manifest.json").read_text(encoding="utf-8"))
    assert manifest["application"]["excluded_tracked_app_bundles"] == ["Legacy.APP"]


def test_portable_verifier_accepts_legacy_v1_manifest_without_exclusion_receipt(tmp_path: Path) -> None:
    root, runtime = fixture_project(tmp_path)
    report = build_portable_macos_app(root, runtime, output_dir=root / "out")
    app = Path(report["app_path"])
    manifest_path = app / "Contents" / "Resources" / "portable-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["application"].pop("excluded_tracked_app_bundle_count")
    manifest["application"].pop("excluded_tracked_app_bundles")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = verify_portable_macos_app(app)

    assert result["status"] == "PORTABLE_APP_VERIFIED"
    checks = {row["name"]: row for row in result["checks"]}
    assert checks["application_exclusion_receipt"]["status"] == "PASS"
    assert checks["application_exclusion_receipt"]["detail"] == "legacy_v1_manifest_without_exclusion_receipt"


def test_portable_verifier_rejects_manifest_declared_nested_app_bundle(tmp_path: Path) -> None:
    root, runtime = fixture_project(tmp_path)
    report = build_portable_macos_app(root, runtime, output_dir=root / "out")
    app = Path(report["app_path"])
    nested = app / "Contents" / "Resources" / "app" / "Injected.app" / "Contents"
    nested.mkdir(parents=True)
    injected = nested / "Info.plist"
    injected.write_text("<plist/>\n", encoding="utf-8")

    manifest_path = app / "Contents" / "Resources" / "portable-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rel = injected.relative_to(app / "Contents" / "Resources" / "app").as_posix()
    manifest["application"]["members"].append(
        {"path": rel, "bytes": injected.stat().st_size, "sha256": hashlib.sha256(injected.read_bytes()).hexdigest()}
    )
    manifest["application"]["members"].sort(key=lambda row: row["path"])
    manifest["application"]["member_count"] = len(manifest["application"]["members"])
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = verify_portable_macos_app(app)

    assert result["status"] == "PORTABLE_APP_VERIFY_FAILED"
    assert "application_excludes_nested_app_bundles" in result["failed_checks"]


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
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["release_version"] = "9.9.9"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    result = verify_portable_macos_app(app)
    assert result["status"] == "PORTABLE_APP_VERIFY_FAILED"
    assert "release_version_matches_package" in result["failed_checks"]
