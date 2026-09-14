from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

RUNTIME_IDENTITY_SCHEMA_VERSION = "mucaro-runtime-identity/1.0"
DEFAULT_PACKAGES = ("mucaro-ai", "fastapi", "pydantic", "shapely", "pypdf")


def _package_versions(packages: Iterable[str]) -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for package in packages:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def current_git_head(project_root: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    head = proc.stdout.strip()
    return head if len(head) == 40 else None


def build_runtime_identity(
    *,
    project_root: Path,
    runtime: str,
    skill_id: str,
    skill_version: str,
    skill_digest: str,
    packages: Iterable[str] = DEFAULT_PACKAGES,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": RUNTIME_IDENTITY_SCHEMA_VERSION,
        "runtime": runtime,
        "skill_id": skill_id,
        "skill_version": skill_version,
        "skill_digest": skill_digest,
        "git_head": current_git_head(project_root),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "package_versions": _package_versions(packages),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["runtime_identity_sha256"] = hashlib.sha256(canonical).hexdigest()
    return payload
