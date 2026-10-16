from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

from .coding_workspace import CodingWorkspaceError, inspect_repository, resolve_repository
from .config import Settings, settings
from .permission_engine import PermissionEngineError, PermissionRequest, require
from .local_tools import resolve_local_tool
from .permissions import is_within_path

DEFAULT_PATCH_MAX_BYTES = 512 * 1024
MAX_PATCH_MAX_BYTES = 2 * 1024 * 1024


def _patch_limit(value: int | None) -> int:
    if value is None:
        return DEFAULT_PATCH_MAX_BYTES
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0 or value > MAX_PATCH_MAX_BYTES:
        raise CodingWorkspaceError(
            "ERROR_CODING_LIMIT_INVALID",
            f"max_bytes must be an integer from 1 through {MAX_PATCH_MAX_BYTES} bytes.",
        )
    return value


def _validate_relative_path(relative: str) -> str:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise CodingWorkspaceError("ERROR_CODING_PATCH_PATH_OUTSIDE_REPOSITORY", f"Patch path escapes repository: {relative}")
    if candidate.parts and candidate.parts[0] == ".git":
        raise CodingWorkspaceError("ERROR_CODING_GIT_ADMIN_PATH_BLOCKED", f"Patch targets .git administrative path: {relative}")
    if not candidate.parts:
        raise CodingWorkspaceError("ERROR_CODING_PATCH_HEADER_INVALID", "Patch contains an empty repository path.")
    return candidate.as_posix()


def _git_executable() -> str:
    git = resolve_local_tool("git")
    if not git:
        raise CodingWorkspaceError("ERROR_CODING_GIT_UNAVAILABLE", "Git is not bundled in this Mucaro runtime.")
    return git


def _git_numstat_paths(repo_root: Path, patch_bytes: bytes) -> list[str]:
    try:
        proc = subprocess.run(
            [_git_executable(), "apply", "--numstat", "-z", "-"],
            cwd=repo_root,
            input=patch_bytes,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise CodingWorkspaceError("ERROR_CODING_GIT_UNAVAILABLE", "git executable is not available.") from exc
    except subprocess.TimeoutExpired as exc:
        raise CodingWorkspaceError("ERROR_CODING_GIT_TIMEOUT", "git apply --numstat timed out.") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip() or "git apply --numstat rejected the patch"
        raise CodingWorkspaceError("ERROR_CODING_PATCH_FORMAT", detail)

    paths: set[str] = set()
    for record in proc.stdout.split(b"\0"):
        if not record:
            continue
        parts = record.split(b"\t", 2)
        if len(parts) != 3 or not parts[2]:
            raise CodingWorkspaceError("ERROR_CODING_PATCH_NUMSTAT_INVALID", "Git returned an invalid NUL-delimited numstat record.")
        try:
            relative = parts[2].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CodingWorkspaceError(
                "ERROR_CODING_PATCH_PATH_ENCODING",
                "Patch path is not valid UTF-8; exact candidate identity cannot be represented safely.",
            ) from exc
        paths.add(_validate_relative_path(relative))
    if not paths:
        raise CodingWorkspaceError("ERROR_CODING_PATCH_FORMAT", "Patch did not yield any Git numstat paths.")
    return sorted(paths)


def _rename_copy_sources(patch: str) -> list[str]:
    paths: set[str] = set()
    for line in patch.splitlines():
        for prefix in ("rename from", "rename to", "copy from", "copy to"):
            marker = f"{prefix} "
            if not line.startswith(marker):
                continue
            raw = line[len(marker):]
            if not raw:
                raise CodingWorkspaceError("ERROR_CODING_PATCH_HEADER_INVALID", f"Invalid patch path header: {line}")
            if raw.startswith('"'):
                raise CodingWorkspaceError(
                    "ERROR_CODING_PATCH_QUOTED_PATH_UNRESOLVED",
                    "Quoted rename/copy source paths are not normalized heuristically; preserve the patch for explicit adjudication.",
                )
            paths.add(_validate_relative_path(raw))
            break
    return sorted(paths)


def _parse_diff_paths(repo_root: Path, patch: str, patch_bytes: bytes) -> list[str]:
    if not any(line.startswith("diff --git ") for line in patch.splitlines()):
        raise CodingWorkspaceError("ERROR_CODING_PATCH_FORMAT", "Patch must contain at least one 'diff --git' file header.")
    paths = set(_git_numstat_paths(repo_root, patch_bytes))
    paths.update(_rename_copy_sources(patch))
    return sorted(paths)


def _git_apply_check(repo_root: Path, patch_bytes: bytes) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            [_git_executable(), "apply", "--check", "--whitespace=error-all", "-"],
            cwd=repo_root,
            input=patch_bytes,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise CodingWorkspaceError("ERROR_CODING_GIT_UNAVAILABLE", "git executable is not available.") from exc
    except subprocess.TimeoutExpired as exc:
        raise CodingWorkspaceError("ERROR_CODING_GIT_TIMEOUT", "git apply --check timed out.") from exc
    detail = proc.stderr.decode("utf-8", errors="replace").strip() or proc.stdout.decode("utf-8", errors="replace").strip()
    return proc.returncode == 0, detail


def _path_preimage(repo_root: Path, relative: str) -> dict[str, Any]:
    target = (repo_root / relative).resolve()
    if not is_within_path(target, repo_root):
        raise CodingWorkspaceError("ERROR_CODING_PATCH_PATH_OUTSIDE_REPOSITORY", f"Patch path resolves outside repository: {relative}")
    if not target.exists():
        return {"path": relative, "state": "MISSING", "byte_size": None, "sha256": None}
    if not target.is_file():
        return {"path": relative, "state": "NON_FILE", "byte_size": None, "sha256": None}
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise CodingWorkspaceError("ERROR_CODING_FILE_READ", f"Unable to hash patch preimage: {relative}") from exc
    return {
        "path": relative,
        "state": "EXISTS",
        "byte_size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def validate_patch_proposal(
    repo_root: str | Path,
    patch: str,
    *,
    cfg: Settings = settings,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    if not isinstance(patch, str) or not patch:
        raise CodingWorkspaceError("ERROR_CODING_PATCH_REQUIRED", "Patch must be a non-empty unified diff string.")
    limit = _patch_limit(max_bytes)
    patch_bytes = patch.encode("utf-8")
    if len(patch_bytes) > limit:
        raise CodingWorkspaceError(
            "ERROR_CODING_PATCH_TOO_LARGE",
            f"Patch is {len(patch_bytes)} bytes, exceeding the approved {limit}-byte ceiling.",
        )

    context = resolve_repository(repo_root, cfg)
    try:
        require(PermissionRequest(capability="git.read", subject="coding_patch.validate_patch_proposal", target=str(context.repo_root), origin="operator"), cfg=cfg)
    except PermissionEngineError as exc:
        raise CodingWorkspaceError(exc.status, exc.detail) from exc
    paths = _parse_diff_paths(context.repo_root, patch, patch_bytes)
    preimages = [_path_preimage(context.repo_root, path) for path in paths]
    state = inspect_repository(context.repo_root, cfg=cfg)
    dirty = set(state.get("dirty_paths") or [])
    dirty_overlap = sorted(dirty.intersection(paths))
    applicable, apply_detail = _git_apply_check(context.repo_root, patch_bytes)

    if not applicable:
        status = "CODING_PATCH_CHECK_REJECTED"
    elif dirty_overlap:
        status = "CODING_PATCH_CHECK_WARN"
    else:
        status = "CODING_PATCH_CHECK_OK"

    return {
        "status": status,
        "applicable": applicable,
        "repo_root": str(context.repo_root),
        "head_sha": state["head_sha"],
        "branch": state["branch"],
        "dirty_status": state["dirty_status"],
        "patch_sha256": hashlib.sha256(patch_bytes).hexdigest(),
        "patch_bytes": len(patch_bytes),
        "patch_path_count": len(paths),
        "patch_paths": paths,
        "preimages": preimages,
        "dirty_overlap": dirty_overlap,
        "git_apply_detail": apply_detail or None,
        "mutation_performed": False,
        "max_bytes": limit,
        "warnings": (["CODING_PATCH_DIRTY_OVERLAP"] if dirty_overlap else []) + (["CODING_PATCH_NOT_APPLICABLE"] if not applicable else []),
        "limitations": [
            "proposal validation is read-only and never applies the patch",
            "effective target paths are parsed by Git through NUL-delimited numstat output rather than inferred from whitespace-sensitive headers",
            "rename/copy source paths are preserved exactly when unquoted; genuinely quoted source headers fail closed for explicit adjudication",
            "path identity is repository-relative and does not prove semantic equivalence across renamed files",
            "preimage SHA-256 proves byte identity only",
            "future application must revalidate HEAD, dirty state, path boundaries, and preimage hashes at execution time",
        ],
        "falsification_criteria": [
            "reject patches without explicit diff --git headers or Git-parsable numstat paths",
            "reject traversal and .git administrative targets from Git's effective path set or rename/copy source headers",
            "reject non-UTF-8 effective target paths rather than normalize them heuristically",
            "reject patch payloads above the approved byte ceiling",
            "do not claim applicability when git apply --check rejects the proposal",
            "do not silently ignore dirty overlap with proposed target paths",
        ],
    }
