"""Remote Git operations for coding from chat: clone a GitHub repository and push a work branch.

Both contact the network and change state, so both are approval-gated tools. Neither ever
forces, rewrites history, touches a protected branch, or passes credentials: Git uses the
person's own configured credential helper or SSH key, and prompts are disabled so a missing
credential fails instead of hanging.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .coding_lifecycle import PROTECTED_BRANCHES
from .coding_workspace import CodingWorkspaceError, inspect_repository, resolve_repository
from .config import Settings, settings
from .local_tools import resolve_local_tool
from .permission_engine import PermissionEngineError, PermissionRequest, require
from .permissions import DirectoryPermissionError, ensure_directory_permission, grant_directory, is_within_path

CODING_REMOTE_SCHEMA_VERSION = "mucaro-coding-remote/1.0"
WORKSPACES_DIRNAME = "workspaces"
DEFAULT_CLONE_DEPTH = 50
MAX_CLONE_DEPTH = 1000
MAX_CLONE_BYTES = 500 * 1024 * 1024
CLONE_TIMEOUT_SECONDS = 300
PUSH_TIMEOUT_SECONDS = 120
_NAME = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}"
# https only for cloning: no credentials in the URL, no other host, no path tricks.
_CLONE_URL = re.compile(rf"^https://github\.com/(?P<owner>{_NAME})/(?P<name>{_NAME}?)(?:\.git)?/?$")
_REMOTE_URLS = (
    re.compile(rf"^https://github\.com/(?P<owner>{_NAME})/(?P<name>{_NAME}?)(?:\.git)?/?$"),
    re.compile(rf"^git@github\.com:(?P<owner>{_NAME})/(?P<name>{_NAME}?)(?:\.git)?$"),
    re.compile(rf"^ssh://git@github\.com/(?P<owner>{_NAME})/(?P<name>{_NAME}?)(?:\.git)?/?$"),
)
_SHA = re.compile(r"^[0-9a-f]{40}$")


class CodingRemoteError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _receipt_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    # Never wait on a username, password or host-key question: fail instead.
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo", "GCM_INTERACTIVE": "never", "GIT_LFS_SKIP_SMUDGE": "1"})
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    return env


def _git(cwd: Path, args: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    git = resolve_local_tool("git")
    if not git:
        raise CodingRemoteError("ERROR_CODING_GIT_UNAVAILABLE", "Git is not bundled in this Mucaro runtime.")
    try:
        return subprocess.run([git, *args], cwd=cwd, text=True, capture_output=True, check=False, timeout=timeout, env=_git_env())
    except OSError as exc:
        raise CodingRemoteError("ERROR_CODING_GIT_UNAVAILABLE", "Git could not be started.") from exc
    except subprocess.TimeoutExpired as exc:
        raise CodingRemoteError("ERROR_CODING_REMOTE_TIMEOUT", f"git {args[0]} exceeded {timeout}s") from exc


def _require(capability: str, subject: str, target: str, cfg: Settings) -> None:
    try:
        require(PermissionRequest(capability=capability, subject=subject, target=target, origin="operator"), cfg=cfg)
    except PermissionEngineError as exc:
        raise CodingRemoteError(exc.status, exc.detail) from exc


def _clean(detail: str) -> str:
    """Git's message without anything that looks like a credential in a URL."""
    return re.sub(r"://[^/@\s]+@", "://", (detail or "").strip())[:600]


def parse_github_remote(url: str) -> str | None:
    """owner/name for a GitHub remote URL (https or ssh), else None."""
    for pattern in _REMOTE_URLS:
        match = pattern.fullmatch((url or "").strip())
        if match and match.group("name") and match.group("name") not in {".", ".."}:
            return f"{match.group('owner')}/{match.group('name').removesuffix('.git')}"
    return None


def github_remote_of(repo_root: str | Path, cfg: Settings = settings, *, remote: str = "origin") -> str | None:
    """owner/name of the repository's GitHub remote, or None when it has none. Read-only."""
    try:
        context = resolve_repository(repo_root, cfg)
        proc = _git(context.repo_root, ["remote", "get-url", remote], timeout=20)
    except (CodingWorkspaceError, CodingRemoteError):
        return None
    return parse_github_remote(proc.stdout) if proc.returncode == 0 else None


def workspaces_root(cfg: Settings = settings) -> Path:
    return cfg.data_dir / WORKSPACES_DIRNAME


def _directory_size(path: Path, ceiling: int) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
            if total > ceiling:
                return total
    return total


def clone_repository(url: str, *, cfg: Settings = settings, depth: int | None = None) -> dict[str, Any]:
    """Shallow-clone one public or accessible GitHub repository into Mucaro's workspace folder."""
    match = _CLONE_URL.fullmatch(url.strip()) if isinstance(url, str) else None
    if not match or not match.group("name") or match.group("name") in {".", ".."}:
        raise CodingRemoteError("ERROR_CODING_CLONE_URL_INVALID", "Only https://github.com/<owner>/<name> URLs can be cloned.")
    owner, name = match.group("owner"), match.group("name").removesuffix(".git")
    if not name:
        raise CodingRemoteError("ERROR_CODING_CLONE_URL_INVALID", "Repository name is missing.")
    clone_depth = DEFAULT_CLONE_DEPTH if depth is None else depth
    if not isinstance(clone_depth, int) or isinstance(clone_depth, bool) or not 1 <= clone_depth <= MAX_CLONE_DEPTH:
        raise CodingRemoteError("ERROR_CODING_CLONE_DEPTH_INVALID", f"depth must be 1..{MAX_CLONE_DEPTH}.")
    canonical_url = f"https://github.com/{owner}/{name}.git"
    root = workspaces_root(cfg)
    destination = root / f"{owner}__{name}"
    if not is_within_path(destination.resolve(), cfg.data_dir.resolve()):
        raise CodingRemoteError("ERROR_CODING_CLONE_DESTINATION", "Clone destination escapes the data directory.")
    if destination.exists():
        raise CodingRemoteError(
            "ERROR_CODING_CLONE_EXISTS", f"{owner}/{name} is already cloned at {destination}. Use that folder or remove it first."
        )
    _require("network.public_http", "coding_remote.clone_repository", canonical_url, cfg)
    _require("filesystem.write", "coding_remote.clone_repository", str(destination), cfg)
    _require("git.mutate", "coding_remote.clone_repository", str(destination), cfg)
    cfg.ensure_dirs()
    root.mkdir(parents=True, exist_ok=True)

    def discard() -> None:
        shutil.rmtree(destination, ignore_errors=True)

    try:
        proc = _git(
            root,
            ["-c", "protocol.file.allow=never", "-c", "protocol.ext.allow=never", "clone", "--depth", str(clone_depth),
             "--single-branch", "--no-tags", "--no-recurse-submodules", "--", canonical_url, str(destination)],
            timeout=CLONE_TIMEOUT_SECONDS,
        )
    except CodingRemoteError:
        discard()
        raise
    if proc.returncode != 0:
        discard()
        raise CodingRemoteError("ERROR_CODING_CLONE_FAILED", _clean(proc.stderr or proc.stdout or "git clone failed"))
    size = _directory_size(destination, MAX_CLONE_BYTES)
    if size > MAX_CLONE_BYTES:
        discard()
        raise CodingRemoteError("ERROR_CODING_CLONE_TOO_LARGE", f"Clone exceeds {MAX_CLONE_BYTES} bytes and was removed.")
    grant = None
    try:
        ensure_directory_permission(destination, cfg)
    except DirectoryPermissionError:
        # A data directory outside the project (packaged app): the approved clone is the grant, for this folder only.
        try:
            grant = grant_directory(destination, cfg, granted_by="repo.clone approval", reason=f"cloned {owner}/{name}")
        except DirectoryPermissionError as exc:
            discard()
            raise CodingRemoteError(exc.status, exc.detail) from exc
    try:
        state = inspect_repository(destination, cfg=cfg)
    except CodingWorkspaceError as exc:
        discard()
        raise CodingRemoteError(exc.status, exc.detail) from exc
    payload = {
        "schema_version": CODING_REMOTE_SCHEMA_VERSION,
        "status": "CODING_CLONE_OK",
        "repo_root": state["repo_root"],
        "remote_url": canonical_url,
        "remote_repo": f"{owner}/{name}",
        "head_sha": state["head_sha"],
        "branch": state["branch"],
        "depth": clone_depth,
        "byte_size": size,
        "directory_grant_added": grant is not None,
        "limitations": [
            "shallow, single-branch clone without tags or submodules; history beyond the depth is absent",
            "Git LFS content is not downloaded",
            "private repositories need the person's own Git credential helper; Mucaro supplies no credentials",
        ],
    }
    payload["receipt_sha256"] = _receipt_hash(payload)
    return payload


def push_work_branch(
    repo_root: str | Path,
    *,
    expected_head_sha: str,
    expected_branch: str,
    remote: str = "origin",
    cfg: Settings = settings,
) -> dict[str, Any]:
    """Push exactly the approved commit of a clean, non-protected work branch to its GitHub remote. Never forces."""
    if not isinstance(expected_head_sha, str) or not _SHA.fullmatch(expected_head_sha):
        raise CodingRemoteError("ERROR_CODING_PUSH_HEAD_REQUIRED", "expected_head_sha must be a 40-character commit SHA.")
    if not isinstance(expected_branch, str) or not expected_branch.strip():
        raise CodingRemoteError("ERROR_CODING_PUSH_BRANCH_REQUIRED", "expected_branch is required.")
    if remote != "origin":
        raise CodingRemoteError("ERROR_CODING_PUSH_REMOTE_INVALID", "Only the origin remote can be pushed to.")
    try:
        context = resolve_repository(repo_root, cfg)
        state = inspect_repository(context.repo_root, cfg=cfg)
    except CodingWorkspaceError as exc:
        raise CodingRemoteError(exc.status, exc.detail) from exc
    root = context.repo_root
    if _git(root, ["check-ref-format", "--branch", expected_branch], timeout=20).returncode != 0:
        raise CodingRemoteError("ERROR_CODING_BRANCH_INVALID", expected_branch)
    if expected_branch in PROTECTED_BRANCHES:
        raise CodingRemoteError("ERROR_CODING_PROTECTED_BRANCH", f"{expected_branch} is never pushed by Mucaro.")
    if state["branch"] != expected_branch:
        raise CodingRemoteError("ERROR_CODING_PUSH_BRANCH_DRIFT", f"expected {expected_branch}, observed {state['branch']}")
    if state["head_sha"] != expected_head_sha:
        raise CodingRemoteError("ERROR_CODING_PUSH_HEAD_DRIFT", f"expected {expected_head_sha}, observed {state['head_sha']}")
    if state["dirty_status"] != "CLEAN":
        raise CodingRemoteError("ERROR_CODING_PUSH_DIRTY_REPOSITORY", "Only a clean, committed work branch can be pushed.")
    url_proc = _git(root, ["remote", "get-url", "--push", remote], timeout=20)
    remote_repo = parse_github_remote(url_proc.stdout) if url_proc.returncode == 0 else None
    if remote_repo is None:
        raise CodingRemoteError("ERROR_CODING_PUSH_REMOTE_INVALID", "The origin remote is missing or is not a GitHub repository.")
    _require("git.mutate", "coding_remote.push_work_branch", str(root), cfg)
    _require("network.public_http", "coding_remote.push_work_branch", f"https://github.com/{remote_repo}", cfg)

    refspec = f"{expected_head_sha}:refs/heads/{expected_branch}"
    # No --force and no '+': Git refuses anything that is not a fast-forward of the remote branch.
    proc = _git(root, ["push", "--porcelain", "--no-verify", remote, refspec], timeout=PUSH_TIMEOUT_SECONDS)
    if proc.returncode != 0:
        detail = _clean(proc.stderr or proc.stdout or "git push failed")
        rejected = "[rejected]" in (proc.stdout + proc.stderr) or "non-fast-forward" in (proc.stdout + proc.stderr)
        raise CodingRemoteError("ERROR_CODING_PUSH_REJECTED" if rejected else "ERROR_CODING_PUSH_FAILED", detail)
    listed = _git(root, ["ls-remote", "--heads", remote, f"refs/heads/{expected_branch}"], timeout=60)
    remote_sha = listed.stdout.split()[0] if listed.returncode == 0 and listed.stdout.split() else None
    if remote_sha != expected_head_sha:
        raise CodingRemoteError("ERROR_CODING_PUSH_UNVERIFIED", f"remote branch reports {remote_sha}, expected {expected_head_sha}")
    payload = {
        "schema_version": CODING_REMOTE_SCHEMA_VERSION,
        "status": "CODING_PUSH_OK",
        "repo_root": str(root),
        "remote": remote,
        "remote_repo": remote_repo,
        "branch": expected_branch,
        "head_sha": expected_head_sha,
        "remote_head_sha": remote_sha,
        "forced": False,
        "invariants": {
            "head_exact": True,
            "branch_exact": True,
            "repository_clean": True,
            "protected_branch_not_pushed": expected_branch not in PROTECTED_BRANCHES,
            "remote_head_verified": remote_sha == expected_head_sha,
            "never_forced": True,
        },
        "limitations": [
            "pushes one branch to origin on github.com only; never forces and never pushes main or master",
            "local pre-push hooks are skipped so repository scripts cannot run during the push",
        ],
    }
    payload["receipt_sha256"] = _receipt_hash(payload)
    return payload
