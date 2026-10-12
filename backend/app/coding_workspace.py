from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings, settings
from .local_tools import resolve_local_tool
from .permissions import DirectoryPermissionError, ensure_directory_permission, is_within_path

DEFAULT_FILE_MAX_BYTES = 256 * 1024
MAX_FILE_MAX_BYTES = 2 * 1024 * 1024
DEFAULT_DIFF_MAX_BYTES = 512 * 1024
MAX_DIFF_MAX_BYTES = 2 * 1024 * 1024
SAFE_GIT_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


class CodingWorkspaceError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class RepositoryContext:
    selected_path: Path
    repo_root: Path
    permission_scope: str
    permission_root: str


def _git_process(repo_root: Path, args: list[str], *, timeout_seconds: int = 20) -> subprocess.CompletedProcess[str]:
    git = resolve_local_tool("git")
    if not git:
        raise CodingWorkspaceError(
            "ERROR_CODING_GIT_UNAVAILABLE",
            "Git is not bundled in this Mucaro runtime.",
        )
    try:
        return subprocess.run(
            [git, *args],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except OSError as exc:
        raise CodingWorkspaceError("ERROR_CODING_GIT_UNAVAILABLE", "Bundled Git could not be started.") from exc
    except subprocess.TimeoutExpired as exc:
        raise CodingWorkspaceError("ERROR_CODING_GIT_TIMEOUT", f"git command timed out: {' '.join(args)}") from exc


def _git_output(repo_root: Path, args: list[str], *, timeout_seconds: int = 20) -> str:
    proc = _git_process(repo_root, args, timeout_seconds=timeout_seconds)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "git command failed").strip()
        raise CodingWorkspaceError("ERROR_CODING_GIT", detail)
    return proc.stdout


def _split_nul(value: str) -> list[str]:
    return [item for item in value.split("\0") if item]


def _bounded_limit(value: int | None, *, default: int, maximum: int, label: str) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0 or value > maximum:
        raise CodingWorkspaceError(
            "ERROR_CODING_LIMIT_INVALID",
            f"{label} must be an integer from 1 through {maximum} bytes.",
        )
    return value


def resolve_repository(repo_root: str | Path, cfg: Settings = settings) -> RepositoryContext:
    try:
        selected = Path(repo_root).expanduser().resolve()
        selected_permission = ensure_directory_permission(selected, cfg)
    except DirectoryPermissionError as exc:
        raise CodingWorkspaceError(exc.status, exc.detail) from exc

    if not selected.is_dir():
        raise CodingWorkspaceError("ERROR_CODING_REPOSITORY_NOT_DIRECTORY", f"Repository selection is not a directory: {selected}")

    top_proc = _git_process(selected, ["rev-parse", "--show-toplevel"])
    if top_proc.returncode != 0 or not top_proc.stdout.strip():
        raise CodingWorkspaceError("ERROR_CODING_REPOSITORY_NOT_FOUND", f"No Git repository found at: {selected}")
    canonical_root = Path(top_proc.stdout.strip()).expanduser().resolve()

    try:
        root_permission = ensure_directory_permission(canonical_root, cfg)
    except DirectoryPermissionError as exc:
        raise CodingWorkspaceError(exc.status, exc.detail) from exc

    if not is_within_path(selected, canonical_root):
        raise CodingWorkspaceError(
            "ERROR_CODING_REPOSITORY_BOUNDARY",
            f"Selected path is not within the resolved repository root: {selected}",
        )

    return RepositoryContext(
        selected_path=selected,
        repo_root=canonical_root,
        permission_scope=str(root_permission.get("scope") or selected_permission.get("scope") or "unknown"),
        permission_root=str(root_permission.get("canonical_root") or canonical_root),
    )


def _repository_state(context: RepositoryContext) -> dict[str, Any]:
    repo_root = context.repo_root
    head_sha = _git_output(repo_root, ["rev-parse", "HEAD"]).strip()
    branch_proc = _git_process(repo_root, ["symbolic-ref", "--quiet", "--short", "HEAD"])
    branch = branch_proc.stdout.strip() if branch_proc.returncode == 0 else None

    staged = sorted(_split_nul(_git_output(repo_root, ["diff", "--cached", "--name-only", "-z", "--"])))
    unstaged = sorted(_split_nul(_git_output(repo_root, ["diff", "--name-only", "-z", "--"])))
    untracked = sorted(_split_nul(_git_output(repo_root, ["ls-files", "--others", "--exclude-standard", "-z"])))
    tracked = _split_nul(_git_output(repo_root, ["ls-files", "-z"]))
    dirty_paths = sorted(set(staged) | set(unstaged) | set(untracked))

    return {
        "repo_root": str(repo_root),
        "selected_path": str(context.selected_path),
        "permission_scope": context.permission_scope,
        "permission_root": context.permission_root,
        "head_sha": head_sha,
        "branch": branch,
        "head_state": "ATTACHED" if branch else "DETACHED",
        "dirty_status": "DIRTY" if dirty_paths else "CLEAN",
        "tracked_file_count": len(tracked),
        "dirty_path_count": len(dirty_paths),
        "staged_files": staged,
        "unstaged_files": unstaged,
        "untracked_files": untracked,
        "dirty_paths": dirty_paths,
        "ignored_files_reported": False,
    }


def inspect_repository(repo_root: str | Path, *, cfg: Settings = settings) -> dict[str, Any]:
    context = resolve_repository(repo_root, cfg)
    state = _repository_state(context)
    return {
        "status": "CODING_REPOSITORY_STATUS_OK",
        **state,
        "limitations": [
            "read-only repository inspection only",
            "ignored files are intentionally not enumerated",
            "rename/copy status is not separately classified in this bounded status receipt",
            "submodules and worktree relationships are not expanded in this receipt",
        ],
        "falsification_criteria": [
            "reject if the selected path or resolved repository root lacks directory permission",
            "reject if Git cannot resolve an exact repository root and HEAD",
            "treat any staged, unstaged, or untracked path as DIRTY",
        ],
    }


def read_repository_file(
    repo_root: str | Path,
    relative_path: str,
    *,
    cfg: Settings = settings,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    context = resolve_repository(repo_root, cfg)
    limit = _bounded_limit(max_bytes, default=DEFAULT_FILE_MAX_BYTES, maximum=MAX_FILE_MAX_BYTES, label="max_bytes")
    requested = Path(relative_path)
    if requested.is_absolute() or not relative_path.strip():
        raise CodingWorkspaceError("ERROR_CODING_FILE_PATH_INVALID", "File path must be a non-empty repository-relative path.")
    if requested.parts and requested.parts[0] == ".git":
        raise CodingWorkspaceError("ERROR_CODING_GIT_ADMIN_PATH_BLOCKED", "Reading .git administrative files is blocked by the coding workspace boundary.")

    target = (context.repo_root / requested).resolve()
    if not is_within_path(target, context.repo_root):
        raise CodingWorkspaceError("ERROR_CODING_FILE_OUTSIDE_REPOSITORY", f"File resolves outside repository root: {relative_path}")
    try:
        ensure_directory_permission(target, cfg)
    except DirectoryPermissionError as exc:
        raise CodingWorkspaceError(exc.status, exc.detail) from exc
    if not target.exists() or not target.is_file():
        raise CodingWorkspaceError("ERROR_CODING_FILE_NOT_FOUND", f"Repository file not found: {relative_path}")

    try:
        size = target.stat().st_size
    except OSError as exc:
        raise CodingWorkspaceError("ERROR_CODING_FILE_READ", f"Unable to inspect repository file: {relative_path}") from exc
    if size > limit:
        raise CodingWorkspaceError(
            "ERROR_CODING_FILE_TOO_LARGE",
            f"Repository file is {size} bytes, exceeding the approved {limit}-byte read ceiling.",
        )
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise CodingWorkspaceError("ERROR_CODING_FILE_READ", f"Unable to read repository file: {relative_path}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    normalized_relative = target.relative_to(context.repo_root).as_posix()
    tracked_proc = _git_process(context.repo_root, ["ls-files", "--error-unmatch", "--", normalized_relative])
    tracked = tracked_proc.returncode == 0

    try:
        content = raw.decode("utf-8")
        encoding_state = "UTF8"
        line_count = 0 if not content else content.count("\n") + (0 if content.endswith("\n") else 1)
    except UnicodeDecodeError:
        content = None
        encoding_state = "BINARY_OR_NON_UTF8"
        line_count = None

    return {
        "status": "CODING_FILE_READ_OK",
        "repo_root": str(context.repo_root),
        "head_sha": _git_output(context.repo_root, ["rev-parse", "HEAD"]).strip(),
        "relative_path": normalized_relative,
        "tracked": tracked,
        "byte_size": len(raw),
        "sha256": digest,
        "encoding_state": encoding_state,
        "line_count": line_count,
        "content": content,
        "max_bytes": limit,
        "limitations": [
            "UTF-8 text is returned exactly; non-UTF-8/binary content is not decoded or normalized",
            ".git administrative files are blocked",
            "file identity is byte-level SHA-256 only and does not prove semantic identity",
        ],
        "falsification_criteria": [
            "reject if path escapes the selected repository after symlink resolution",
            "reject .git administrative paths",
            "reject if byte size exceeds the approved read ceiling",
            "do not synthesize text for non-UTF-8 content",
        ],
    }


def inspect_repository_diff(
    repo_root: str | Path,
    *,
    cfg: Settings = settings,
    base: str = "HEAD",
    max_bytes: int | None = None,
) -> dict[str, Any]:
    context = resolve_repository(repo_root, cfg)
    limit = _bounded_limit(max_bytes, default=DEFAULT_DIFF_MAX_BYTES, maximum=MAX_DIFF_MAX_BYTES, label="max_bytes")
    if not isinstance(base, str) or not SAFE_GIT_REF.fullmatch(base) or base.startswith("-") or ".." in base:
        raise CodingWorkspaceError("ERROR_CODING_BASE_REF_INVALID", "base must be a bounded local Git ref or commit identifier.")

    verified_base = _git_output(context.repo_root, ["rev-parse", "--verify", f"{base}^{{commit}}"] ).strip()
    changed_files = sorted(_split_nul(_git_output(context.repo_root, ["diff", "--name-only", "-z", verified_base, "--"])))
    diff_text = _git_output(context.repo_root, ["diff", "--no-ext-diff", "--no-color", verified_base, "--"], timeout_seconds=30)
    diff_bytes = diff_text.encode("utf-8")
    if len(diff_bytes) > limit:
        raise CodingWorkspaceError(
            "ERROR_CODING_DIFF_TOO_LARGE",
            f"Diff is {len(diff_bytes)} bytes, exceeding the approved {limit}-byte output ceiling.",
        )

    state = _repository_state(context)
    return {
        "status": "CODING_DIFF_OK",
        "repo_root": str(context.repo_root),
        "head_sha": state["head_sha"],
        "branch": state["branch"],
        "head_state": state["head_state"],
        "dirty_status": state["dirty_status"],
        "dirty_path_count": state["dirty_path_count"],
        "staged_files": state["staged_files"],
        "unstaged_files": state["unstaged_files"],
        "untracked_files": state["untracked_files"],
        "dirty_paths": state["dirty_paths"],
        "base_ref": base,
        "base_commit_sha": verified_base,
        "changed_files": changed_files,
        "changed_file_count": len(changed_files),
        "untracked_file_count": len(state["untracked_files"]),
        "diff_sha256": hashlib.sha256(diff_bytes).hexdigest(),
        "diff_bytes": len(diff_bytes),
        "diff": diff_text,
        "max_bytes": limit,
        "limitations": [
            "Git diff excludes untracked file contents; untracked paths are reported separately",
            "rename/copy status is not separately classified in this bounded diff receipt",
            "this operation is read-only and does not propose or apply a patch",
            "binary diff semantics are not expanded beyond Git's textual diff representation",
        ],
        "falsification_criteria": [
            "reject invalid or unresolved base refs",
            "reject diff output above the approved byte ceiling rather than silently truncating it",
            "do not classify the worktree CLEAN when staged, unstaged, or untracked paths exist",
        ],
    }
