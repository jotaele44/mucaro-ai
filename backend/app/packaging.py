from __future__ import annotations

import json
import tomllib
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

    blockers = [
        "Hosted GitHub Actions startup is unresolved; governed release tags still require hosted CI success.",
        "Distribution artifact format is not finalized beyond editable install plus desktop launcher smoke path.",
    ]
    checks.append(_check("distribution_policy", "WARN", "; ".join(blockers)))

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
            "python3 scripts/certify_release.py",
            'python3 scripts/certify_fresh_clone.py --ref "$(git rev-parse HEAD)"',
        ],
    }
