from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import tomllib
import zipfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .contracts import ContractViolation, validate_contract
from .execution_records import build_artifact_identity
from .macos_app_store import build_macos_app_store_readiness
from .release_receipts import load_release_receipt, receipt_path


ROOT = Path(__file__).resolve().parents[2]
PYTHON_PACKAGE_NAME = "mucaro-ai"
FRONTEND_PACKAGE_NAME = "mucaro-ai-frontend"
CLI_ENTRYPOINT = "backend.app.cli:main"
SOURCE_ARTIFACT_FORMAT_V1 = "source_zip_with_json_manifest_v1"
SOURCE_ARTIFACT_FORMAT_V2 = "source_zip_with_json_manifest_v2"
CURRENT_SOURCE_ARTIFACT_FORMAT = SOURCE_ARTIFACT_FORMAT_V2

REQUIRED_PACKAGE_INPUTS = [
    "pyproject.toml",
    "README.md",
    "scripts/mucaro.py",
    "scripts/launch_app.py",
    "scripts/bootstrap_macos.sh",
    "scripts/bootstrap_windows.ps1",
    "scripts/certify_release.py",
    "scripts/certify_fresh_clone.py",
    "scripts/package_desktop.py",
    "scripts/certify_source_artifact.py",
    "scripts/certify_python_distribution.py",
    "docs/PACKAGING_POLICY.md",
    "docs/APPLE_APP_STORE_SECURITY.md",
    "_architecture/macos_app_store_security_profile.json",
    "_architecture/schemas/macos-app-store-candidate-receipt.schema.json",
    "_architecture/schemas/macos-app-store-vulnerability-scan.schema.json",
    "_architecture/schemas/macos-app-store-malware-scan.schema.json",
    "packaging/macos/MucaroAI-AppStore.entitlements",
    "packaging/macos/PrivacyInfo.xcprivacy",
    "packaging/runtime/macos-x86_64.json",
    "packaging/runtime/requirements-macos-x86_64.lock",
    "frontend/package.json",
    "frontend/package-lock.json",
]

OPTIONAL_GENERATED_OUTPUTS = [
    "frontend/dist/index.html",
]
DISTRIBUTION_MANIFEST_SCHEMA_VERSION = "mucaro_distribution_manifest_v1"
DISTRIBUTION_MANIFEST_DIR = Path("data") / "exports" / "distribution_manifests"
MACOS_APP_BUNDLE_SCHEMA_VERSION = "mucaro_macos_app_bundle_v1"
MACOS_APP_BUNDLE_NAME = "Mucaro AI.app"
MACOS_APP_BUNDLE_DIR = Path("data") / "exports" / "desktop_apps"
REQUIRED_DISTRIBUTION_RECEIPTS = [
    ("local_release", "CERTIFY_RELEASE_OK"),
    ("fresh_clone", "FRESH_CLONE_CERT_OK"),
    ("source_artifact", "SOURCE_ARTIFACT_CERT_OK"),
    ("python_dist", "PYTHON_DIST_CERT_OK"),
    ("answer_trace_quality", "ANSWER_TRACE_QUALITY_CERT_OK"),
    ("retrieval_quality", "RETRIEVAL_QUALITY_CERT_OK"),
    ("gis_quality", "GIS_QUALITY_CERT_OK"),
    ("operator_workflow", "OPERATOR_WORKFLOW_CERT_OK"),
]


def _check(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def _load_toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object in {path}")
    return payload


def _string_value(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value
    return None


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | 0o755)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _project_version(root: Path) -> str:
    try:
        project = _load_toml(root / "pyproject.toml").get("project", {})
    except (OSError, tomllib.TOMLDecodeError):
        return "0.0.0"
    return _string_value(project, "version") or "0.0.0"


def _file_summary(path_value: str | None, root: Path) -> dict[str, Any]:
    if not path_value:
        return {"path": None, "exists": False, "sha256": None, "size_bytes": None}
    path = _resolve_package_path(root, Path(path_value))
    exists = path.exists() and path.is_file()
    return {
        "path": str(path),
        "exists": exists,
        "sha256": _sha256_file(path) if exists else None,
        "size_bytes": path.stat().st_size if exists else None,
    }


def _git_output(root: Path, args: list[str]) -> tuple[bool, str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return proc.returncode == 0, proc.stdout.strip()


def _tracked_source_files(root: Path) -> tuple[list[str], str | None]:
    ok, output = _git_output(root, ["ls-files", "-z"])
    if not ok:
        return [], output or "git ls-files failed"
    files = sorted(path for path in output.split("\0") if path)
    if not files:
        return [], "no tracked files"
    return files, None


def _resolve_package_path(root: Path, value: Path) -> Path:
    expanded = value.expanduser()
    return expanded if expanded.is_absolute() else root / expanded


def _zip_path_safe(path: str) -> bool:
    parsed = PurePosixPath(path)
    return not parsed.is_absolute() and ".." not in parsed.parts and bool(parsed.parts)


def build_desktop_package_manifest(root: Path = ROOT) -> dict[str, Any]:
    required = [
        "scripts/launch_app.py",
        "scripts/bootstrap_macos.sh",
        "scripts/bootstrap_windows.ps1",
        "frontend/package-lock.json",
        "pyproject.toml",
        "packaging/runtime/macos-x86_64.json",
        "packaging/runtime/requirements-macos-x86_64.lock",
    ]
    missing = [path for path in required if not (root / path).exists()]
    metadata_error = None
    version = None
    try:
        pyproject = _load_toml(root / "pyproject.toml")
        project = pyproject.get("project", {})
        version = _string_value(project, "version")
        if not version:
            metadata_error = "missing project.version"
    except (OSError, tomllib.TOMLDecodeError) as exc:
        metadata_error = str(exc)
    ready = not missing and metadata_error is None
    app_bundle_path = root / MACOS_APP_BUNDLE_DIR / MACOS_APP_BUNDLE_NAME
    return {
        "status": "DESKTOP_PACKAGE_READY" if ready else "DESKTOP_PACKAGE_INCOMPLETE",
        "version": version,
        "entrypoint": "scripts/launch_app.py",
        "macos_app_bundle": {
            "name": MACOS_APP_BUNDLE_NAME,
            "path": str(app_bundle_path),
            "exists": app_bundle_path.exists() and app_bundle_path.is_dir(),
            "builder": "mucaro package macos-app",
        },
        "required_files": required,
        "missing_files": missing,
        "metadata_error": metadata_error,
        "outputs": ["frontend/dist", "local Python editable install", "launcher smoke path", str(MACOS_APP_BUNDLE_DIR / MACOS_APP_BUNDLE_NAME)],
    }


def build_macos_app_bundle(
    root: Path = ROOT,
    *,
    output_dir: Path | None = None,
    python_executable: str | None = None,
) -> dict[str, Any]:
    output_dir = _resolve_package_path(root, output_dir or root / MACOS_APP_BUNDLE_DIR)
    app_path = output_dir / MACOS_APP_BUNDLE_NAME
    contents = app_path / "Contents"
    macos_dir = contents / "MacOS"
    resources_dir = contents / "Resources"
    executable_path = macos_dir / "mucaro-ai"
    plist_path = contents / "Info.plist"
    receipt_path = resources_dir / "mucaro-app-receipt.json"
    # Kept as an API compatibility argument; setup provisions its own interpreter.
    del python_executable

    checks: list[dict[str, str]] = []
    required_files = [
        root / "scripts" / "native_start.sh",
        root / "scripts" / "one_click.sh",
        root / "scripts" / "one_click.py",
        root / "pyproject.toml",
    ]
    missing_files = [str(path.relative_to(root)) for path in required_files if not path.exists()]
    checks.append(_check("required_runtime_inputs", "PASS" if not missing_files else "FAIL", "all present" if not missing_files else f"missing={','.join(missing_files)}"))

    ok, head_sha = _git_output(root, ["rev-parse", "HEAD"])
    checks.append(_check("git_head", "PASS" if ok else "WARN", head_sha or "unavailable"))

    failed = [check for check in checks if check["status"] == "FAIL"]
    if failed:
        return {
            "status": "MACOS_APP_BUNDLE_FAILED",
            "schema_version": MACOS_APP_BUNDLE_SCHEMA_VERSION,
            "project_root": str(root),
            "app_path": str(app_path),
            "checks": checks,
            "failed_checks": [check["name"] for check in failed],
        }

    launcher = f"""#!/bin/bash
set -euo pipefail
REPO_ROOT={shlex.quote(str(root))}
cd "${{REPO_ROOT}}"
exec /bin/bash scripts/native_start.sh "${{REPO_ROOT}}" "$@"
"""
    info_plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>Mucaro AI</string>
  <key>CFBundleDisplayName</key>
  <string>Mucaro AI</string>
  <key>CFBundleExecutable</key>
  <string>mucaro-ai</string>
  <key>CFBundleIdentifier</key>
  <string>ai.mucaro.source-launcher</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>{_project_version(root)}</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
"""
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_executable(executable_path, launcher)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(info_plist, encoding="utf-8")
    resources_dir.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema_version": MACOS_APP_BUNDLE_SCHEMA_VERSION,
        "status": "MACOS_APP_BUNDLE_READY",
        "created_utc": _utc_now(),
        "project_root": str(root),
        "app_path": str(app_path),
        "bundle_name": MACOS_APP_BUNDLE_NAME,
        "head_sha": head_sha if ok else None,
        "python_executable": "provisioned by scripts/one_click.sh",
        "entrypoint": "scripts/native_start.sh",
        "frontend_dist": "frontend/dist/index.html",
        "scope": "source_bound_local_macos_app_wrapper",
        "limitations": [
            "this bundle launches the checked-out source tree on this machine",
            "portable signed installer, embedded Python runtime, upgrade, and rollback remain future packaging work",
        ],
    }
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    verify_report = verify_macos_app_bundle(app_path, root=root)
    checks.extend(verify_report.get("checks", []))
    failed = [check for check in checks if check["status"] == "FAIL"]
    return {
        "status": "MACOS_APP_BUNDLE_READY" if not failed else "MACOS_APP_BUNDLE_FAILED",
        "schema_version": MACOS_APP_BUNDLE_SCHEMA_VERSION,
        "project_root": str(root),
        "app_path": str(app_path),
        "head_sha": head_sha if ok else None,
        "receipt": {
            "path": str(receipt_path),
            "sha256": _sha256_file(receipt_path),
        },
        "executable": {
            "path": str(executable_path),
            "sha256": _sha256_file(executable_path),
            "mode": oct(executable_path.stat().st_mode & 0o777),
        },
        "checks": checks,
        "failed_checks": [check["name"] for check in failed],
        "verification": verify_report,
    }


def verify_macos_app_bundle(app_path: Path, *, root: Path = ROOT) -> dict[str, Any]:
    app_path = _resolve_package_path(root, app_path)
    contents = app_path / "Contents"
    executable_path = contents / "MacOS" / "mucaro-ai"
    plist_path = contents / "Info.plist"
    receipt_path = contents / "Resources" / "mucaro-app-receipt.json"
    checks: list[dict[str, str]] = [
        _check("app_bundle_exists", "PASS" if app_path.exists() and app_path.is_dir() else "FAIL", str(app_path)),
        _check("bundle_extension", "PASS" if app_path.name.endswith(".app") else "FAIL", app_path.name),
        _check("contents_dir", "PASS" if contents.exists() and contents.is_dir() else "FAIL", str(contents)),
        _check("info_plist", "PASS" if plist_path.exists() else "FAIL", str(plist_path)),
        _check("launcher_executable", "PASS" if executable_path.exists() and os.access(executable_path, os.X_OK) else "FAIL", str(executable_path)),
        _check("receipt_json", "PASS" if receipt_path.exists() else "FAIL", str(receipt_path)),
    ]

    receipt: dict[str, Any] = {}
    if receipt_path.exists():
        try:
            receipt = _load_json(receipt_path)
            checks.append(_check("receipt_schema", "PASS" if receipt.get("schema_version") == MACOS_APP_BUNDLE_SCHEMA_VERSION else "FAIL", str(receipt.get("schema_version"))))
            checks.append(_check("receipt_entrypoint", "PASS" if receipt.get("entrypoint") == "scripts/native_start.sh" else "FAIL", str(receipt.get("entrypoint"))))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            checks.append(_check("receipt_parse", "FAIL", str(exc)))

    if plist_path.exists():
        plist_text = plist_path.read_text(encoding="utf-8", errors="replace")
        checks.append(_check("plist_executable_binding", "PASS" if "<string>mucaro-ai</string>" in plist_text else "FAIL", "CFBundleExecutable=mucaro-ai"))
        checks.append(_check("plist_package_type", "PASS" if "<string>APPL</string>" in plist_text else "FAIL", "CFBundlePackageType=APPL"))

    if executable_path.exists():
        launcher_text = executable_path.read_text(encoding="utf-8", errors="replace")
        checks.append(_check("launcher_points_to_repo", "PASS" if str(root) in launcher_text else "FAIL", str(root)))
        checks.append(_check("launcher_points_to_native_start", "PASS" if "scripts/native_start.sh" in launcher_text else "FAIL", "scripts/native_start.sh"))
        checks.append(_check("launcher_forwards_arguments", "PASS" if '"$@"' in launcher_text else "FAIL", "bundle executable forwards launch_app arguments"))

    failed = [check for check in checks if check["status"] == "FAIL"]
    return {
        "status": "MACOS_APP_BUNDLE_VERIFIED" if not failed else "MACOS_APP_BUNDLE_VERIFY_FAILED",
        "schema_version": MACOS_APP_BUNDLE_SCHEMA_VERSION,
        "project_root": str(root),
        "app_path": str(app_path),
        "checks": checks,
        "failed_checks": [check["name"] for check in failed],
        "receipt": {
            "path": str(receipt_path),
            "sha256": _sha256_file(receipt_path) if receipt_path.exists() else None,
        },
        "executable": {
            "path": str(executable_path),
            "sha256": _sha256_file(executable_path) if executable_path.exists() else None,
            "mode": oct(executable_path.stat().st_mode & 0o777) if executable_path.exists() else None,
        },
    }


def build_package_status(root: Path = ROOT) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    pyproject_path = root / "pyproject.toml"
    frontend_package_path = root / "frontend" / "package.json"
    package_lock_path = root / "frontend" / "package-lock.json"

    missing_inputs = [path for path in REQUIRED_PACKAGE_INPUTS if not (root / path).exists()]
    checks.append(
        _check(
            "required_package_inputs",
            "PASS" if not missing_inputs else "FAIL",
            "all present" if not missing_inputs else f"missing={','.join(missing_inputs)}",
        )
    )

    py_version: str | None = None
    py_name: str | None = None
    scripts = {}
    try:
        pyproject = _load_toml(pyproject_path)
        project = pyproject.get("project", {})
        py_name = _string_value(project, "name")
        py_version = _string_value(project, "version")
        scripts = project.get("scripts", {})
        metadata_ok = py_name == PYTHON_PACKAGE_NAME and py_version is not None
        checks.append(
            _check(
                "python_package_metadata",
                "PASS" if metadata_ok else "FAIL",
                f"name={py_name} version={py_version}",
            )
        )
    except (OSError, tomllib.TOMLDecodeError, KeyError) as exc:
        checks.append(_check("python_package_metadata", "FAIL", str(exc)))

    frontend_version: str | None = None
    frontend_name: str | None = None
    try:
        frontend_package = _load_json(frontend_package_path)
        frontend_name = _string_value(frontend_package, "name")
        frontend_version = _string_value(frontend_package, "version")
        frontend_metadata_ok = frontend_name == FRONTEND_PACKAGE_NAME and frontend_version is not None
        checks.append(
            _check(
                "frontend_package_metadata",
                "PASS" if frontend_metadata_ok else "FAIL",
                f"name={frontend_name} version={frontend_version}",
            )
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        checks.append(_check("frontend_package_metadata", "FAIL", str(exc)))

    try:
        package_lock = _load_json(package_lock_path)
        lock_name = _string_value(package_lock, "name")
        lock_version = _string_value(package_lock, "version")
        lock_root = package_lock.get("packages", {}).get("") if isinstance(package_lock.get("packages"), dict) else {}
        lock_root_name = _string_value(lock_root, "name") if isinstance(lock_root, dict) else None
        lock_root_version = _string_value(lock_root, "version") if isinstance(lock_root, dict) else None
        lock_matches = (
            lock_name == frontend_name
            and lock_version == frontend_version
            and lock_root_name == frontend_name
            and lock_root_version == frontend_version
        )
        checks.append(
            _check(
                "frontend_lockfile",
                "PASS" if lock_matches else "FAIL",
                (
                    f"package={lock_name}:{lock_version} "
                    f"root={lock_root_name}:{lock_root_version} "
                    f"expected={frontend_name}:{frontend_version}"
                ),
            )
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        checks.append(_check("frontend_lockfile", "FAIL", str(exc)))

    versions_match = bool(py_version and frontend_version and py_version == frontend_version)
    checks.append(
        _check(
            "version_consistency",
            "PASS" if versions_match else "FAIL",
            f"pyproject={py_version} frontend={frontend_version}",
        )
    )

    cli_entrypoint = scripts.get("mucaro") if isinstance(scripts, dict) else None
    checks.append(
        _check(
            "cli_entrypoint",
            "PASS" if cli_entrypoint == CLI_ENTRYPOINT else "FAIL",
            str(cli_entrypoint),
        )
    )

    launcher = root / "scripts" / "launch_app.py"
    checks.append(_check("desktop_launcher_entrypoint", "PASS" if launcher.exists() else "FAIL", _relative(launcher, root)))

    desktop_manifest = build_desktop_package_manifest(root) if pyproject_path.exists() else {"status": "DESKTOP_PACKAGE_INCOMPLETE"}
    checks.append(
        _check(
            "desktop_package_manifest",
            "PASS" if desktop_manifest.get("status") == "DESKTOP_PACKAGE_READY" else "FAIL",
            str(desktop_manifest.get("status")),
        )
    )
    app_bundle_path = root / MACOS_APP_BUNDLE_DIR / MACOS_APP_BUNDLE_NAME
    if app_bundle_path.exists():
        app_verification = verify_macos_app_bundle(app_bundle_path, root=root)
        checks.append(
            _check(
                "macos_app_bundle",
                "PASS" if app_verification.get("status") == "MACOS_APP_BUNDLE_VERIFIED" else "FAIL",
                str(app_verification.get("status")),
            )
        )
    else:
        checks.append(
            _check(
                "macos_app_bundle",
                "WARN",
                f"missing={MACOS_APP_BUNDLE_DIR / MACOS_APP_BUNDLE_NAME}",
            )
        )

    missing_outputs = [path for path in OPTIONAL_GENERATED_OUTPUTS if not (root / path).exists()]
    checks.append(
        _check(
            "generated_outputs",
            "PASS" if not missing_outputs else "WARN",
            "all present" if not missing_outputs else f"missing={','.join(missing_outputs)}",
        )
    )

    policy_path = root / "docs" / "PACKAGING_POLICY.md"
    checks.append(
        _check(
            "release_artifact_policy",
            "PASS" if policy_path.exists() else "FAIL",
            _relative(policy_path, root),
        )
    )
    checks.append(
        _check(
            "distribution_policy",
            "PASS",
            "source ZIP plus JSON manifest under data/exports/packages; signed installer deferred",
        )
    )
    app_store_readiness = build_macos_app_store_readiness(root)
    app_store_status = str(app_store_readiness.get("status"))
    checks.append(
        _check(
            "mac_app_store_security_profile",
            "FAIL" if app_store_status == "MAC_APP_STORE_READINESS_FAIL" else "WARN",
            app_store_status,
        )
    )
    blockers = [
        "Hosted release governance remains open until exact-head CI and configured branch/tag controls are proven.",
        "Mac App Store signing, provisioning, upload processing, and Apple App Review remain external gates.",
    ]
    checks.append(_check("hosted_release_gate", "WARN", blockers[0]))

    failed = [check for check in checks if check["status"] == "FAIL"]
    warned = [check for check in checks if check["status"] == "WARN"]
    status = "PACKAGE_FAIL" if failed else "PACKAGE_WARN" if warned else "PACKAGE_READY"
    return {
        "status": status,
        "project_root": str(root),
        "python_package": {"name": py_name, "version": py_version, "entrypoint": cli_entrypoint},
        "frontend_package": {"name": frontend_name, "version": frontend_version},
        "desktop_package": desktop_manifest,
        "checks": checks,
        "blockers": blockers,
        "next_commands": [
            "python3 scripts/package_desktop.py --self-test",
            "python3 scripts/package_desktop.py --build-app",
            "PYTHONPATH=.:backend python3 scripts/mucaro.py package macos-app --format text",
            "PYTHONPATH=.:backend python3 scripts/mucaro.py package app-store-audit --strict --format text",
            "PYTHONPATH=.:backend python3 scripts/mucaro.py package build --format text",
            "PYTHONPATH=.:backend python3 scripts/mucaro.py package verify MANIFEST_PATH --require-current-head --format text",
            "python3 scripts/certify_python_distribution.py",
            "python3 scripts/certify_release.py",
            'python3 scripts/certify_fresh_clone.py --ref "$(git rev-parse HEAD)"',
        ],
    }


def build_release_artifacts(
    root: Path = ROOT,
    *,
    output_dir: Path | None = None,
    allow_dirty: bool = False,
) -> dict[str, Any]:
    output_dir = output_dir or root / "data" / "exports" / "packages"
    checks: list[dict[str, str]] = []
    package_status = build_package_status(root)
    failed_package_checks = [check for check in package_status["checks"] if check["status"] == "FAIL"]
    checks.append(
        _check(
            "package_status",
            "PASS" if not failed_package_checks else "FAIL",
            package_status["status"],
        )
    )

    ok, head_sha = _git_output(root, ["rev-parse", "HEAD"])
    checks.append(_check("git_head", "PASS" if ok else "FAIL", head_sha or "missing"))

    ok, status_output = _git_output(root, ["status", "--short"])
    clean = ok and not status_output
    checks.append(
        _check(
            "worktree_clean",
            "PASS" if clean or allow_dirty else "FAIL",
            "clean" if clean else status_output or "git status failed",
        )
    )

    tracked_files, tracked_error = _tracked_source_files(root)
    checks.append(
        _check(
            "tracked_source_files",
            "PASS" if tracked_files else "FAIL",
            f"count={len(tracked_files)}" if tracked_files else str(tracked_error),
        )
    )

    pyproject_path = root / "pyproject.toml"
    try:
        pyproject = _load_toml(pyproject_path)
        project = pyproject.get("project", {})
        version = _string_value(project, "version")
        name = _string_value(project, "name")
    except (OSError, tomllib.TOMLDecodeError):
        version = None
        name = None
    checks.append(
        _check(
            "artifact_identity",
            "PASS" if name == PYTHON_PACKAGE_NAME and version else "FAIL",
            f"name={name} version={version}",
        )
    )

    failed = [check for check in checks if check["status"] == "FAIL"]
    if failed:
        return {
            "status": "PACKAGE_ARTIFACT_BLOCKED",
            "project_root": str(root),
            "output_dir": str(output_dir),
            "checks": checks,
            "artifact": None,
            "manifest": None,
        }

    output_dir.mkdir(parents=True, exist_ok=True)
    if version is None or head_sha is None:  # guarded by the checks above; explicit so it survives python -O
        raise RuntimeError("package version and head must be known before writing the artifact")
    archive_path = output_dir / f"{PYTHON_PACKAGE_NAME}-{version}-{head_sha[:12]}-source.zip"
    manifest_path = output_dir / f"{PYTHON_PACKAGE_NAME}-{version}-{head_sha[:12]}-artifact-manifest.json"
    archive_root = f"{PYTHON_PACKAGE_NAME}-{version}"
    members = []
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative_path in tracked_files:
            source_path = root / relative_path
            if not source_path.is_file():
                continue
            data = source_path.read_bytes()
            info = zipfile.ZipInfo(f"{archive_root}/{relative_path}", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (source_path.stat().st_mode & 0o777) << 16
            archive.writestr(info, data)
            members.append(
                {
                    "path": relative_path,
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
    artifact_identity = build_artifact_identity(
        archive_path,
        artifact_type="EXPORT",
        logical_identity=f"{PYTHON_PACKAGE_NAME}:{version}:{head_sha}:source",
        producer="backend.app.packaging.build_release_artifacts",
        producer_version=version,
        source_path_or_uri=str(archive_path),
    )
    manifest = {
        "artifact_identity": artifact_identity,
        "status": "PACKAGE_ARTIFACT_READY",
        "artifact_format": CURRENT_SOURCE_ARTIFACT_FORMAT,
        "project_root": str(root),
        "package_name": PYTHON_PACKAGE_NAME,
        "version": version,
        "head_sha": head_sha,
        "dirty_allowed": allow_dirty,
        "archive_path": str(archive_path),
        "archive_sha256": _sha256_file(archive_path),
        "archive_size_bytes": archive_path.stat().st_size,
        "member_count": len(members),
        "members": members,
        "policy": "docs/PACKAGING_POLICY.md",
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "status": "PACKAGE_ARTIFACT_READY",
        "project_root": str(root),
        "output_dir": str(output_dir),
        "checks": checks,
        "artifact": {
            "path": str(archive_path),
            "sha256": manifest["archive_sha256"],
            "size_bytes": manifest["archive_size_bytes"],
        },
        "manifest": {
            "path": str(manifest_path),
            "sha256": _sha256_file(manifest_path),
            "member_count": len(members),
        },
    }


def verify_release_artifact(
    manifest_path: Path,
    *,
    root: Path = ROOT,
    require_current_head: bool = False,
) -> dict[str, Any]:
    manifest_path = _resolve_package_path(root, manifest_path)
    checks: list[dict[str, str]] = []
    checks.append(_check("manifest_exists", "PASS" if manifest_path.exists() else "FAIL", str(manifest_path)))
    if not manifest_path.exists():
        return {
            "status": "PACKAGE_ARTIFACT_VERIFY_FAILED",
            "project_root": str(root),
            "manifest_path": str(manifest_path),
            "checks": checks,
            "artifact": None,
            "manifest": None,
        }

    try:
        manifest = _load_json(manifest_path)
        checks.append(_check("manifest_json", "PASS", "valid JSON object"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        checks.append(_check("manifest_json", "FAIL", str(exc)))
        return {
            "status": "PACKAGE_ARTIFACT_VERIFY_FAILED",
            "project_root": str(root),
            "manifest_path": str(manifest_path),
            "checks": checks,
            "artifact": None,
            "manifest": None,
        }

    artifact_format = manifest.get("artifact_format")
    package_name = manifest.get("package_name")
    version = manifest.get("version")
    head_sha = manifest.get("head_sha")
    members = manifest.get("members")
    checks.append(
        _check(
            "manifest_identity",
            "PASS"
            if (
                artifact_format in {SOURCE_ARTIFACT_FORMAT_V1, SOURCE_ARTIFACT_FORMAT_V2}
                and package_name == PYTHON_PACKAGE_NAME
                and isinstance(version, str)
                and isinstance(head_sha, str)
            )
            else "FAIL",
            f"format={artifact_format} package={package_name} version={version} head_sha={head_sha}",
        )
    )

    if require_current_head:
        head_ok, current_head = _git_output(root, ["rev-parse", "HEAD"])
        checks.append(
            _check(
                "current_head_binding",
                "PASS" if head_ok and current_head == head_sha else "FAIL",
                f"manifest={head_sha} current={current_head if head_ok else 'unavailable'}",
            )
        )

    archive_value = manifest.get("archive_path")
    archive_path = _resolve_package_path(manifest_path.parent, Path(archive_value)) if isinstance(archive_value, str) else None
    checks.append(_check("archive_path", "PASS" if archive_path and archive_path.exists() else "FAIL", str(archive_path or archive_value)))
    artifact_identity = manifest.get("artifact_identity")
    identity_required = artifact_format == SOURCE_ARTIFACT_FORMAT_V2
    identity_ok = isinstance(artifact_identity, dict)
    if identity_ok:
        try:
            validate_contract("artifact_identity", artifact_identity)
        except ContractViolation:
            identity_ok = False
    legacy_v1_without_identity = artifact_format == SOURCE_ARTIFACT_FORMAT_V1 and artifact_identity is None
    checks.append(
        _check(
            "artifact_identity_contract",
            "PASS" if identity_ok or legacy_v1_without_identity else "FAIL",
            (
                str(artifact_identity.get("artifact_id"))
                if isinstance(artifact_identity, dict)
                else "legacy_v1_not_required"
                if legacy_v1_without_identity
                else "missing_required_v2_identity"
            ),
        )
    )

    manifest_member_count = manifest.get("member_count")
    members_is_list = isinstance(members, list)
    member_rows = members if members_is_list else []
    member_paths = [member.get("path") for member in member_rows if isinstance(member, dict)]
    duplicate_member_paths = sorted(path for path, count in Counter(member_paths).items() if isinstance(path, str) and count > 1)
    malformed_members = 0
    for member in member_rows:
        if not isinstance(member, dict):
            malformed_members += 1
            continue
        path = member.get("path")
        size_bytes = member.get("size_bytes")
        sha256 = member.get("sha256")
        if not isinstance(path, str) or not _zip_path_safe(path) or not isinstance(size_bytes, int) or not isinstance(sha256, str) or len(sha256) != 64:
            malformed_members += 1
    checks.append(
        _check(
            "manifest_members",
            "PASS"
            if members_is_list
            and isinstance(manifest_member_count, int)
            and manifest_member_count == len(member_rows)
            and not duplicate_member_paths
            and malformed_members == 0
            else "FAIL",
            f"declared={manifest_member_count} actual={len(member_rows)} malformed={malformed_members} duplicates={','.join(duplicate_member_paths) or 'none'}",
        )
    )

    if archive_path and archive_path.exists():
        archive_sha = _sha256_file(archive_path)
        archive_size = archive_path.stat().st_size
        if isinstance(artifact_identity, dict):
            checks.append(
                _check(
                    "artifact_identity_bytes",
                    "PASS"
                    if artifact_identity.get("sha256") == archive_sha
                    and artifact_identity.get("byte_size") == archive_size
                    else "FAIL",
                    f"identity_sha={artifact_identity.get('sha256')} actual_sha={archive_sha}",
                )
            )
        elif identity_required:
            checks.append(_check("artifact_identity_bytes", "FAIL", "required v2 artifact identity missing"))
        checks.append(
            _check(
                "archive_sha256",
                "PASS" if archive_sha == manifest.get("archive_sha256") else "FAIL",
                f"manifest={manifest.get('archive_sha256')} actual={archive_sha}",
            )
        )
        checks.append(
            _check(
                "archive_size",
                "PASS" if archive_size == manifest.get("archive_size_bytes") else "FAIL",
                f"manifest={manifest.get('archive_size_bytes')} actual={archive_size}",
            )
        )

        try:
            with zipfile.ZipFile(archive_path) as archive:
                infos = [info for info in archive.infolist() if not info.is_dir()]
                duplicate_archive_paths = sorted(path for path, count in Counter(info.filename for info in infos).items() if count > 1)
                unsafe = sorted(info.filename for info in infos if not _zip_path_safe(info.filename))
                archive_root = f"{package_name}-{version}"
                expected_names = {f"{archive_root}/{member['path']}" for member in member_rows if isinstance(member, dict) and isinstance(member.get("path"), str)}
                actual_names = {info.filename for info in infos}
                checks.append(_check("archive_member_paths_safe", "PASS" if not unsafe else "FAIL", ",".join(unsafe) or "safe"))
                checks.append(
                    _check(
                        "archive_member_duplicates",
                        "PASS" if not duplicate_archive_paths else "FAIL",
                        ",".join(duplicate_archive_paths) or "none",
                    )
                )
                checks.append(
                    _check(
                        "archive_member_set",
                        "PASS" if actual_names == expected_names and not duplicate_archive_paths else "FAIL",
                        f"expected={len(expected_names)} actual={len(actual_names)} missing={len(expected_names - actual_names)} extra={len(actual_names - expected_names)}",
                    )
                )
                member_by_name = {f"{archive_root}/{member['path']}": member for member in member_rows if isinstance(member, dict) and isinstance(member.get("path"), str)}
                mismatches: list[str] = []
                for info in infos:
                    member = member_by_name.get(info.filename)
                    if not member:
                        continue
                    data = archive.read(info)
                    if info.file_size != member.get("size_bytes") or _sha256_bytes(data) != member.get("sha256"):
                        mismatches.append(info.filename)
                checks.append(_check("archive_member_hashes", "PASS" if not mismatches else "FAIL", ",".join(mismatches) or "all match"))
        except zipfile.BadZipFile as exc:
            checks.append(_check("archive_zip_readable", "FAIL", str(exc)))

    failed = [check for check in checks if check["status"] == "FAIL"]
    return {
        "status": "PACKAGE_ARTIFACT_VERIFY_FAILED" if failed else "PACKAGE_ARTIFACT_VERIFIED",
        "project_root": str(root),
        "manifest_path": str(manifest_path),
        "checks": checks,
        "artifact": {
            "path": str(archive_path) if archive_path else None,
            "sha256": manifest.get("archive_sha256"),
            "size_bytes": manifest.get("archive_size_bytes"),
        },
        "manifest": {
            "artifact_format": artifact_format,
            "package_name": package_name,
            "version": version,
            "head_sha": head_sha,
            "member_count": manifest_member_count,
            "sha256": _sha256_file(manifest_path),
        },
    }


def build_distribution_manifest(
    root: Path = ROOT,
    *,
    output_path: Path | None = None,
    save: bool = False,
) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    ok, head_sha = _git_output(root, ["rev-parse", "HEAD"])
    checks.append(_check("git_head", "PASS" if ok else "FAIL", head_sha or "missing"))

    tag_ok, tags_output = _git_output(root, ["tag", "--points-at", "HEAD"])
    tags_at_head = sorted(line for line in tags_output.splitlines() if line.strip()) if tag_ok else []
    checks.append(_check("tags_at_head", "PASS" if tag_ok else "WARN", ",".join(tags_at_head) or "none"))

    package = build_package_status(root)
    failed_package_checks = [check for check in package.get("checks", []) if check.get("status") == "FAIL"]
    checks.append(_check("package_status", "PASS" if not failed_package_checks else "FAIL", str(package.get("status"))))

    receipts: dict[str, dict[str, Any]] = {}
    for receipt_type, ok_status in REQUIRED_DISTRIBUTION_RECEIPTS:
        receipt = load_release_receipt(root, receipt_type=receipt_type, head_sha=head_sha if ok else None)
        receipt_file = receipt_path(root, receipt_type, head_sha if ok else "UNKNOWN_HEAD")
        receipts[receipt_type] = {
            "required_status": ok_status,
            "path": str(receipt_file),
            "exists": receipt_file.exists(),
            "file_sha256": _sha256_file(receipt_file) if receipt_file.exists() else None,
            "status": receipt.get("status") if receipt else None,
            "created_utc": receipt.get("created_utc") if receipt else None,
        }
        receipt_passed = receipt is not None and receipt.get("status") == ok_status
        checks.append(
            _check(
                f"receipt:{receipt_type}",
                "PASS" if receipt_passed else "FAIL",
                f"status={receipt.get('status') if receipt else 'missing'} required={ok_status}",
            )
        )

    source_receipt = load_release_receipt(root, receipt_type="source_artifact", head_sha=head_sha if ok else None)
    source_payload = source_receipt.get("payload", {}) if isinstance(source_receipt, dict) else {}
    python_receipt = load_release_receipt(root, receipt_type="python_dist", head_sha=head_sha if ok else None)
    python_payload = python_receipt.get("payload", {}) if isinstance(python_receipt, dict) else {}
    python_manifest = python_payload.get("manifest", {}) if isinstance(python_payload.get("manifest"), dict) else {}
    artifacts = {
        "source_archive": _file_summary(
            str(source_payload.get("archive_path")) if source_payload.get("archive_path") else None,
            root,
        ),
        "source_manifest": _file_summary(
            str(source_payload.get("manifest_path")) if source_payload.get("manifest_path") else None,
            root,
        ),
        "python_dist_manifest": _file_summary(
            str(python_manifest.get("path")) if python_manifest.get("path") else None,
            root,
        ),
        "wheel": _file_summary(
            str(python_payload.get("wheel", {}).get("path"))
            if isinstance(python_payload.get("wheel"), dict) and python_payload.get("wheel", {}).get("path")
            else None,
            root,
        ),
        "sdist": _file_summary(
            str(python_payload.get("sdist", {}).get("path"))
            if isinstance(python_payload.get("sdist"), dict) and python_payload.get("sdist", {}).get("path")
            else None,
            root,
        ),
    }
    for name, artifact in artifacts.items():
        checks.append(_check(f"artifact:{name}", "PASS" if artifact["exists"] else "FAIL", str(artifact["path"])))

    failed = [check for check in checks if check["status"] == "FAIL"]
    manifest = {
        "schema_version": DISTRIBUTION_MANIFEST_SCHEMA_VERSION,
        "status": "DISTRIBUTION_MANIFEST_READY" if not failed else "DISTRIBUTION_MANIFEST_FAILED",
        "created_utc": _utc_now(),
        "project_root": str(root),
        "package_name": PYTHON_PACKAGE_NAME,
        "version": _load_toml(root / "pyproject.toml").get("project", {}).get("version"),
        "head_sha": head_sha if ok else None,
        "tags_at_head": tags_at_head,
        "scope": "local_distribution_handoff",
        "hosted_governance_required": True,
        "hosted_governance_status": "OPEN",
        "package_status": package.get("status"),
        "checks": checks,
        "failed_checks": [check["name"] for check in failed],
        "receipts": receipts,
        "artifacts": artifacts,
        "blockers": package.get("blockers", []),
        "next_commands": [
            "python3 scripts/certify_release.py",
            f"python3 scripts/certify_fresh_clone.py --ref {head_sha if ok else 'HEAD'}",
            "python3 scripts/certify_source_artifact.py data/exports/packages/MANIFEST.json",
            "python3 scripts/certify_python_distribution.py",
            "python3 -m backend.app.cli package distribution-manifest --save --format text",
            "python3 -m backend.app.cli release state --include-github --format text",
        ],
    }
    if save or output_path is not None:
        destination = output_path or root / DISTRIBUTION_MANIFEST_DIR / f"distribution-manifest-{str(head_sha)[:12]}.json"
        destination = _resolve_package_path(root, destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest["export"] = {
            "path": str(destination),
            "sha256": _sha256_file(destination),
            "size_bytes": destination.stat().st_size,
            "schema_version": DISTRIBUTION_MANIFEST_SCHEMA_VERSION,
        }
    return manifest


def verify_distribution_manifest(
    manifest_path: Path,
    *,
    root: Path = ROOT,
    expected_sha256: str | None = None,
    require_current_head: bool = False,
) -> dict[str, Any]:
    path = _resolve_package_path(root, manifest_path)
    checks: list[dict[str, str]] = []
    checks.append(_check("manifest_exists", "PASS" if path.exists() else "FAIL", str(path)))
    if not path.exists():
        return {
            "status": "DISTRIBUTION_MANIFEST_VERIFY_FAILED",
            "project_root": str(root),
            "manifest_path": str(path),
            "checks": checks,
        }

    actual_sha = _sha256_file(path)
    checks.append(_check("manifest_sha256", "PASS", actual_sha))
    if expected_sha256:
        checks.append(
            _check(
                "expected_sha256",
                "PASS" if actual_sha == expected_sha256 else "FAIL",
                f"expected={expected_sha256} actual={actual_sha}",
            )
        )
    try:
        payload = _load_json(path)
        error = None
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        payload = None
        error = str(exc)
    checks.append(_check("manifest_json", "PASS" if isinstance(payload, dict) and error is None else "FAIL", error or "valid JSON object"))
    if not isinstance(payload, dict) or error is not None:
        return {
            "status": "DISTRIBUTION_MANIFEST_VERIFY_FAILED",
            "project_root": str(root),
            "manifest_path": str(path),
            "artifact": {"sha256": actual_sha, "size_bytes": path.stat().st_size},
            "checks": checks,
        }

    checks.append(
        _check(
            "schema_version",
            "PASS" if payload.get("schema_version") == DISTRIBUTION_MANIFEST_SCHEMA_VERSION else "FAIL",
            str(payload.get("schema_version")),
        )
    )
    if require_current_head:
        head_ok, current_head = _git_output(root, ["rev-parse", "HEAD"])
        checks.append(
            _check(
                "current_head_binding",
                "PASS" if head_ok and payload.get("head_sha") == current_head else "FAIL",
                f"manifest={payload.get('head_sha')} current={current_head if head_ok else 'unavailable'}",
            )
        )

    artifacts = payload.get("artifacts", {})
    if isinstance(artifacts, dict):
        for name, artifact in artifacts.items():
            if not isinstance(artifact, dict):
                checks.append(_check(f"artifact:{name}", "FAIL", "artifact summary is not an object"))
                continue
            artifact_path = artifact.get("path")
            artifact_summary = _file_summary(str(artifact_path) if artifact_path else None, root)
            expected_artifact_sha = artifact.get("sha256")
            artifact_ok = (
                artifact_summary["exists"]
                and isinstance(expected_artifact_sha, str)
                and artifact_summary["sha256"] == expected_artifact_sha
            )
            checks.append(
                _check(
                    f"artifact:{name}",
                    "PASS" if artifact_ok else "FAIL",
                    f"expected={expected_artifact_sha} actual={artifact_summary['sha256']}",
                )
            )
    else:
        checks.append(_check("artifacts", "FAIL", "artifacts is not an object"))

    receipts = payload.get("receipts", {})
    if isinstance(receipts, dict):
        for receipt_type, receipt in receipts.items():
            if not isinstance(receipt, dict):
                checks.append(_check(f"receipt:{receipt_type}", "FAIL", "receipt summary is not an object"))
                continue
            receipt_file = Path(str(receipt.get("path") or ""))
            receipt_file = _resolve_package_path(root, receipt_file)
            expected_receipt_sha = receipt.get("file_sha256")
            actual_receipt_sha = _sha256_file(receipt_file) if receipt_file.exists() else None
            checks.append(
                _check(
                    f"receipt:{receipt_type}",
                    "PASS" if receipt_file.exists() and actual_receipt_sha == expected_receipt_sha else "FAIL",
                    f"expected={expected_receipt_sha} actual={actual_receipt_sha}",
                )
            )
    else:
        checks.append(_check("receipts", "FAIL", "receipts is not an object"))

    failed = [check for check in checks if check["status"] == "FAIL"]
    return {
        "status": "DISTRIBUTION_MANIFEST_VERIFY_FAILED" if failed else "DISTRIBUTION_MANIFEST_VERIFIED",
        "project_root": str(root),
        "manifest_path": str(path),
        "head_sha": payload.get("head_sha"),
        "hosted_governance_status": payload.get("hosted_governance_status"),
        "artifact": {"sha256": actual_sha, "size_bytes": path.stat().st_size},
        "checks": checks,
    }
