from __future__ import annotations

import hashlib
import json
import subprocess
import tomllib
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PYTHON_PACKAGE_NAME = "mucaro-ai"
FRONTEND_PACKAGE_NAME = "mucaro-ai-frontend"
CLI_ENTRYPOINT = "backend.app.cli:main"

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
    "docs/PACKAGING_POLICY.md",
    "frontend/package.json",
    "frontend/package-lock.json",
]

OPTIONAL_GENERATED_OUTPUTS = [
    "frontend/dist/index.html",
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


def build_desktop_package_manifest(root: Path = ROOT) -> dict[str, Any]:
    required = [
        "scripts/launch_app.py",
        "scripts/bootstrap_macos.sh",
        "scripts/bootstrap_windows.ps1",
        "frontend/package-lock.json",
        "pyproject.toml",
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
    return {
        "status": "DESKTOP_PACKAGE_READY" if ready else "DESKTOP_PACKAGE_INCOMPLETE",
        "version": version,
        "entrypoint": "scripts/launch_app.py",
        "required_files": required,
        "missing_files": missing,
        "metadata_error": metadata_error,
        "outputs": ["frontend/dist", "local Python editable install", "launcher smoke path"],
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
    blockers = [
        "Hosted GitHub Actions startup is unresolved; governed release tags still require hosted CI success.",
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
            "PYTHONPATH=.:backend python3 scripts/mucaro.py package build --format text",
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
    assert version is not None
    assert head_sha is not None
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
    manifest = {
        "status": "PACKAGE_ARTIFACT_READY",
        "artifact_format": "source_zip_with_json_manifest_v1",
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
