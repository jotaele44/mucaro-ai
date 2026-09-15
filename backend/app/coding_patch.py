from __future__ import annotations

import hashlib
import shlex
import subprocess
from pathlib import Path
from typing import Any

from .coding_workspace import CodingWorkspaceError, inspect_repository, resolve_repository
from .config import Settings, settings
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


def _prefixed_patch_path(token: str, expected_prefix: str) -> str:
    if token == "/dev/null":
        return ""
    if not token.startswith(expected_prefix) or len(token) <= 2:
        raise CodingWorkspaceError("ERROR_CODING_PATCH_HEADER_INVALID", f"Invalid repository path in patch header: {token}")
    return _validate_relative_path(token[2:])


def _single_header_path(line: str, prefix: str) -> str:
    try:
        parts = shlex.split(line)
    except ValueError as exc:
        raise CodingWorkspaceError("ERROR_CODING_PATCH_HEADER_INVALID", f"Invalid patch header: {line}") from exc
    if len(parts) != 2 or parts[0] != prefix:
        raise CodingWorkspaceError("ERROR_CODING_PATCH_HEADER_INVALID", f"Invalid patch header: {line}")
    return parts[1]


def _parse_diff_paths(patch: str) -> list[str]:
    paths: set[str] = set()
    diff_headers = 0
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            diff_headers += 1
            try:
                parts = shlex.split(line)
            except ValueError as exc:
                raise CodingWorkspaceError("ERROR_CODING_PATCH_HEADER_INVALID", f"Invalid diff header: {line}") from exc
            if len(parts) != 4 or parts[0:2] != ["diff", "--git"]:
                raise CodingWorkspaceError("ERROR_CODING_PATCH_HEADER_INVALID", f"Invalid diff header: {line}")
            for token, expected_prefix in ((parts[2], "a/"), (parts[3], "b/")):
                relative = _prefixed_patch_path(token, expected_prefix)
                if relative:
                    paths.add(relative)
            continue
        if line.startswith("--- "):
            token = _single_header_path(line, "---")
            relative = _prefixed_patch_path(token, "a/")
            if relative:
                paths.add(relative)
            continue
        if line.startswith("+++ "):
            token = _single_header_path(line, "+++")
            relative = _prefixed_patch_path(token, "b/")
            if relative:
                paths.add(relative)
            continue
        for prefix in ("rename from", "rename to", "copy from", "copy to"):
            marker = f"{prefix} "
            if line.startswith(marker):
                relative = line[len(marker):]
                if not relative:
                    raise CodingWorkspaceError("ERROR_CODING_PATCH_HEADER_INVALID", f"Invalid patch path header: {line}")
                paths.add(_validate_relative_path(relative))
                break
    if diff_headers == 0 or not paths:
        raise CodingWorkspaceError("ERROR_CODING_PATCH_FORMAT", "Patch must contain at least one 'diff --git' file header.")
    return sorted(paths)


def _git_apply_check(repo_root: Path, patch: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["git", "apply", "--check", "--whitespace=error-all", "-"],
            cwd=repo_root,
            input=patch,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise CodingWorkspaceError("ERROR_CODING_GIT_UNAVAILABLE", "git executable is not available.") from exc
    except subprocess.TimeoutExpired as exc:
        raise CodingWorkspaceError("ERROR_CODING_GIT_TIMEOUT", "git apply --check timed out.") from exc
    detail = (proc.stderr or proc.stdout or "").strip()
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
    paths = _parse_diff_paths(patch)
    preimages = [_path_preimage(context.repo_root, path) for path in paths]
    state = inspect_repository(context.repo_root, cfg=cfg)
    dirty = set(state.get("dirty_paths") or [])
    dirty_overlap = sorted(dirty.intersection(paths))
    applicable, apply_detail = _git_apply_check(context.repo_root, patch)

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
            "all diff/file/rename/copy headers contribute to the preserved patch candidate set",
            "path identity is repository-relative and does not prove semantic equivalence across renamed files",
            "preimage SHA-256 proves byte identity only",
            "future application must revalidate HEAD, dirty state, path boundaries, and preimage hashes at execution time",
        ],
        "falsification_criteria": [
            "reject patches without explicit diff --git path headers",
            "reject traversal and .git administrative targets in any parsed patch path header",
            "reject patch payloads above the approved byte ceiling",
            "do not claim applicability when git apply --check rejects the proposal",
            "do not silently ignore dirty overlap with proposed target paths",
        ],
    }
