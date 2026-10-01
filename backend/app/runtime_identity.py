from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import sys
from pathlib import Path
from typing import Any, Iterable

RUNTIME_IDENTITY_SCHEMA_VERSION = "mucaro-runtime-identity/1.0"
DEFAULT_PACKAGES = ("mucaro-ai", "fastapi", "pydantic", "shapely", "pypdf")
_SHA40 = re.compile(r"^[0-9a-fA-F]{40}$")


def _package_versions(packages: Iterable[str]) -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for package in packages:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def _normalized_sha(value: str | None) -> str | None:
    candidate = (value or "").strip()
    return candidate.lower() if _SHA40.fullmatch(candidate) else None


def _git_directory(project_root: Path) -> Path | None:
    marker = project_root / ".git"
    if marker.is_dir():
        return marker
    if not marker.is_file():
        return None
    try:
        text = marker.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    prefix = "gitdir:"
    if not text.lower().startswith(prefix):
        return None
    target = text[len(prefix):].strip()
    if not target:
        return None
    candidate = Path(target)
    if not candidate.is_absolute():
        candidate = marker.parent / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    return resolved if resolved.is_dir() else None


def _packed_ref(git_dir: Path, ref_name: str) -> str | None:
    packed = git_dir / "packed-refs"
    try:
        lines = packed.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return None
    for line in lines:
        if not line or line.startswith(("#", "^")):
            continue
        parts = line.split(" ", 1)
        if len(parts) == 2 and parts[1].strip() == ref_name:
            return _normalized_sha(parts[0])
    return None


def _bound_file_head(project_root: Path) -> str | None:
    candidates = (
        ("mucaro_build_identity.json", "source_head"),
        ("portable-manifest.json", "head_sha"),
    )
    for filename, key in candidates:
        path = project_root / filename
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            bound = _normalized_sha(payload.get(key))
            if bound is not None:
                return bound
    return None


def current_git_head(project_root: Path) -> str | None:
    """Resolve source identity without invoking a host Git executable.

    Packaged builds may bind MUCARO_SOURCE_HEAD or ship a build/portable
    manifest containing the source SHA. Source checkouts are read directly
    from .git metadata, including worktree gitdir pointers and packed refs.
    Missing identity remains explicit as None.
    """

    bound = _normalized_sha(os.getenv("MUCARO_SOURCE_HEAD"))
    if bound is not None:
        return bound
    bound = _bound_file_head(project_root)
    if bound is not None:
        return bound

    git_dir = _git_directory(project_root)
    if git_dir is None:
        return None
    try:
        head_text = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None

    direct = _normalized_sha(head_text)
    if direct is not None:
        return direct
    if not head_text.startswith("ref:"):
        return None

    ref_name = head_text[4:].strip()
    if not ref_name or ref_name.startswith("/") or ".." in Path(ref_name).parts:
        return None
    try:
        loose_text = (git_dir / ref_name).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        loose_text = ""
    return _normalized_sha(loose_text) or _packed_ref(git_dir, ref_name)


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
