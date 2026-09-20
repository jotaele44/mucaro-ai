from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

PORTABLE_APP_SCHEMA_VERSION = "mucaro-portable-app/1.0"
PORTABLE_APP_NAME = "Mucaro AI Portable.app"
DEFAULT_RUNTIME_PYTHON = "bin/python3"


class PortableRuntimeError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise PortableRuntimeError(
                "ERROR_PORTABLE_RUNTIME_SYMLINK",
                f"symlinked runtime/application members are not admitted in v1: {path}",
            )
        if path.is_file():
            files.append(path)
    return files


def _manifest_rows(root: Path) -> list[dict[str, Any]]:
    rows = []
    for path in _tree_files(root):
        rel = path.relative_to(root).as_posix()
        rows.append({"path": rel, "bytes": path.stat().st_size, "sha256": _sha256(path)})
    return rows


def _git_tracked_files(root: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise PortableRuntimeError("ERROR_PORTABLE_GIT_FILES", proc.stderr.decode("utf-8", errors="replace"))
    rows = sorted(item.decode("utf-8") for item in proc.stdout.split(b"\0") if item)
    if not rows:
        raise PortableRuntimeError("ERROR_PORTABLE_GIT_FILES", "no tracked files")
    return rows


def _git_head(root: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise PortableRuntimeError("ERROR_PORTABLE_GIT_HEAD", proc.stderr.strip())
    return proc.stdout.strip()


def _require_tracked_files_at_head(root: Path) -> None:
    proc = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise PortableRuntimeError("ERROR_PORTABLE_GIT_STATUS", proc.stderr.strip())
    if proc.stdout.strip():
        raise PortableRuntimeError("ERROR_PORTABLE_WORKTREE_DIRTY", proc.stdout.strip())


def _safe_relative(value: str) -> Path:
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or ".." in parsed.parts or not parsed.parts:
        raise PortableRuntimeError("ERROR_PORTABLE_MEMBER_PATH", value)
    return Path(*parsed.parts)


def build_portable_macos_app(
    root: Path,
    runtime_dir: Path,
    *,
    output_dir: Path,
    runtime_python: str = DEFAULT_RUNTIME_PYTHON,
    runtime_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    runtime_dir = runtime_dir.resolve()
    output_dir = output_dir.resolve()
    if not runtime_dir.is_dir():
        raise PortableRuntimeError("ERROR_PORTABLE_RUNTIME_MISSING", str(runtime_dir))

    runtime_python_rel = _safe_relative(runtime_python)
    source_python = runtime_dir / runtime_python_rel
    if not source_python.is_file() or not os.access(source_python, os.X_OK):
        raise PortableRuntimeError(
            "ERROR_PORTABLE_RUNTIME_PYTHON",
            f"runtime Python is missing or not executable: {source_python}",
        )

    frontend_dist = root / "frontend" / "dist"
    if not (frontend_dist / "index.html").is_file():
        raise PortableRuntimeError(
            "ERROR_PORTABLE_FRONTEND_DIST",
            "frontend/dist/index.html is required before portable packaging",
        )
    _require_tracked_files_at_head(root)

    app_path = output_dir / PORTABLE_APP_NAME
    if app_path.exists():
        shutil.rmtree(app_path)
    resources = app_path / "Contents" / "Resources"
    runtime_target = resources / "runtime"
    app_target = resources / "app"
    macos_dir = app_path / "Contents" / "MacOS"
    macos_dir.mkdir(parents=True, exist_ok=True)

    runtime_source_rows = _manifest_rows(runtime_dir)
    shutil.copytree(runtime_dir, runtime_target, symlinks=False)

    tracked = _git_tracked_files(root)
    copied_tracked: list[str] = []
    for rel in tracked:
        source = root / _safe_relative(rel)
        if not source.is_file():
            continue
        target = app_target / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied_tracked.append(rel)

    dist_target = app_target / "frontend" / "dist"
    if dist_target.exists():
        shutil.rmtree(dist_target)
    shutil.copytree(frontend_dist, dist_target, symlinks=False)

    launcher_path = macos_dir / "mucaro-ai"
    launcher = """#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RESOURCES="$(cd "${HERE}/../Resources" && pwd)"
APP_ROOT="${RESOURCES}/app"
PYTHON_EXECUTABLE="${RESOURCES}/runtime/bin/python3"
cd "${APP_ROOT}"
export PYTHONPATH="${APP_ROOT}:${APP_ROOT}/backend"
unset PYTHONHOME PYTHONINSPECT PYTHONSTARTUP
export PYTHONNOUSERSITE=1
export PYTHONSAFEPATH=1
export PYTHONDONTWRITEBYTECODE=1
exec "${PYTHON_EXECUTABLE}" scripts/launch_app.py "$@"
"""
    launcher_path.write_text(launcher, encoding="utf-8")
    launcher_path.chmod(launcher_path.stat().st_mode | 0o755)

    plist_path = app_path / "Contents" / "Info.plist"
    plist_path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>Mucaro AI</string>
<key>CFBundleDisplayName</key><string>Mucaro AI</string>
<key>CFBundleExecutable</key><string>mucaro-ai</string>
<key>CFBundleIdentifier</key><string>ai.mucaro.portable</string>
<key>CFBundlePackageType</key><string>APPL</string>
</dict></plist>
""",
        encoding="utf-8",
    )

    manifest_path = resources / "portable-manifest.json"
    runtime_rows = _manifest_rows(runtime_target)
    app_rows = _manifest_rows(app_target)
    manifest = {
        "schema_version": PORTABLE_APP_SCHEMA_VERSION,
        "status": "PORTABLE_APP_SOURCE_READY",
        "head_sha": _git_head(root),
        "runtime": {
            "python_path": f"Contents/Resources/runtime/{runtime_python_rel.as_posix()}",
            "source_member_count": len(runtime_source_rows),
            "member_count": len(runtime_rows),
            "members": runtime_rows,
            "provenance": runtime_provenance,
        },
        "application": {
            "tracked_source_count": len(copied_tracked),
            "member_count": len(app_rows),
            "members": app_rows,
            "frontend_index": "Contents/Resources/app/frontend/dist/index.html",
            "entrypoint": "Contents/Resources/app/scripts/launch_app.py",
        },
        "launcher": {
            "path": "Contents/MacOS/mucaro-ai",
            "sha256": _sha256(launcher_path),
            "source_bound_absolute_paths": False,
        },
        "distribution_state": {
            "embedded_runtime": True,
            "bundle_relative_launch": True,
            "signed": False,
            "notarized": False,
            "clean_machine_executed": False,
            "reinstall_executed": False,
            "rollback_executed": False,
        },
        "limitations": [
            "runtime payload provenance and relocatability must be certified independently",
            "structural build success is not clean-machine execution evidence",
            "code signing, notarization, reinstall, upgrade, migration and rollback remain separate release gates",
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return verify_portable_macos_app(app_path, expected_head=manifest["head_sha"])


def verify_portable_macos_app(app_path: Path, *, expected_head: str | None = None) -> dict[str, Any]:
    app_path = app_path.resolve()
    resources = app_path / "Contents" / "Resources"
    manifest_path = resources / "portable-manifest.json"
    launcher_path = app_path / "Contents" / "MacOS" / "mucaro-ai"
    if not manifest_path.is_file():
        raise PortableRuntimeError("ERROR_PORTABLE_MANIFEST_MISSING", str(manifest_path))
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != PORTABLE_APP_SCHEMA_VERSION:
        raise PortableRuntimeError("ERROR_PORTABLE_MANIFEST_SCHEMA", str(payload.get("schema_version")))
    if expected_head is not None and payload.get("head_sha") != expected_head:
        raise PortableRuntimeError("ERROR_PORTABLE_HEAD_MISMATCH", str(payload.get("head_sha")))

    checks: list[dict[str, str]] = []
    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "status": "PASS" if ok else "FAIL", "detail": detail})

    runtime_python = app_path / _safe_relative(payload["runtime"]["python_path"])
    frontend_index = app_path / _safe_relative(payload["application"]["frontend_index"])
    entrypoint = app_path / _safe_relative(payload["application"]["entrypoint"])
    check("runtime_python", runtime_python.is_file() and os.access(runtime_python, os.X_OK), str(runtime_python))
    check("frontend_index", frontend_index.is_file(), str(frontend_index))
    check("application_entrypoint", entrypoint.is_file(), str(entrypoint))
    check("launcher_executable", launcher_path.is_file() and os.access(launcher_path, os.X_OK), str(launcher_path))

    launcher_text = launcher_path.read_text(encoding="utf-8") if launcher_path.is_file() else ""
    check("bundle_relative_launcher", "RESOURCES=" in launcher_text and "runtime/bin/python3" in launcher_text, "launcher resolves embedded resources relative to itself")
    check("launcher_no_source_root", payload["launcher"].get("source_bound_absolute_paths") is False, "manifest declares no source-bound absolute path")
    check(
        "launcher_disables_host_user_site",
        "PYTHONNOUSERSITE=1" in launcher_text and "PYTHONSAFEPATH=1" in launcher_text,
        "launcher excludes host user-site packages and unsafe implicit paths",
    )

    for section_name in ("runtime", "application"):
        base = resources / ("runtime" if section_name == "runtime" else "app")
        expected_rows = payload[section_name].get("members") or []
        actual_rows = _manifest_rows(base)
        check(
            f"{section_name}_member_manifest",
            expected_rows == actual_rows,
            f"expected={len(expected_rows)} actual={len(actual_rows)}",
        )

    runtime_provenance = payload["runtime"].get("provenance")
    if runtime_provenance is not None:
        provenance_path = resources / "runtime" / "runtime-provenance.json"
        check(
            "runtime_provenance_receipt",
            provenance_path.is_file()
            and runtime_provenance.get("receipt_sha256") == _sha256(provenance_path)
            and runtime_provenance.get("status") == "PORTABLE_RUNTIME_VERIFIED",
            str(provenance_path),
        )

    failed = [row for row in checks if row["status"] != "PASS"]
    return {
        "status": "PORTABLE_APP_VERIFIED" if not failed else "PORTABLE_APP_VERIFY_FAILED",
        "schema_version": PORTABLE_APP_SCHEMA_VERSION,
        "app_path": str(app_path),
        "manifest_path": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path),
        "checks": checks,
        "failed_checks": [row["name"] for row in failed],
        "distribution_state": payload.get("distribution_state"),
    }
