from __future__ import annotations

import hashlib
import re
import subprocess
from typing import Any

from .coding_lifecycle import (
    CodingLifecycleError,
    build_pull_request_request,
)
from .coding_workspace import CodingWorkspaceError, inspect_repository
from .config import Settings, settings
from .github_pull_requests import (
    GitHubPullRequestError,
    _local_origin_repo,
    _remote_branch_sha,
)
from .local_tools import resolve_local_tool
from .utils import utc_now

SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
MAX_OUTPUT_BYTES = 128 * 1024


class GitHubBranchPushError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _git_push(
    repo_root: str,
    *,
    refspec: str,
    dry_run: bool,
    timeout_seconds: int = 120,
) -> subprocess.CompletedProcess[str]:
    try:
        git = resolve_local_tool("git")
    except ValueError as exc:
        raise GitHubBranchPushError(
            "ERROR_GITHUB_PUSH_GIT_UNAVAILABLE",
            "Git is not allowlisted.",
        ) from exc
    if not git:
        raise GitHubBranchPushError(
            "ERROR_GITHUB_PUSH_GIT_UNAVAILABLE",
            "Git is not available in this runtime.",
        )

    argv = [git, "-C", repo_root, "push", "--porcelain"]
    if dry_run:
        argv.append("--dry-run")
    argv.extend(["origin", refspec])
    try:
        proc = subprocess.run(
            argv,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitHubBranchPushError(
            "ERROR_GITHUB_PUSH_TIMEOUT",
            f"git push exceeded {timeout_seconds}s",
        ) from exc
    except OSError as exc:
        raise GitHubBranchPushError(
            "ERROR_GITHUB_PUSH_GIT_UNAVAILABLE",
            "Git could not be started.",
        ) from exc

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    if len(stdout.encode("utf-8")) > MAX_OUTPUT_BYTES or len(stderr.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise GitHubBranchPushError(
            "ERROR_GITHUB_PUSH_OUTPUT_TOO_LARGE",
            "git push output exceeded the bounded capture ceiling.",
        )
    return proc


def _output_digest(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    return {
        "exit_code": proc.returncode,
        "stdout_bytes": len(stdout.encode("utf-8")),
        "stderr_bytes": len(stderr.encode("utf-8")),
        "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
    }


def push_pull_request_branch(
    repo_root: str,
    *,
    remote_repo: str,
    expected_head_sha: str,
    head_branch: str,
    base_branch: str,
    title: str,
    body: str,
    expected_request_sha256: str,
    cfg: Settings = settings,
) -> dict[str, Any]:
    if not isinstance(remote_repo, str) or not REPO.fullmatch(remote_repo.strip()):
        raise GitHubBranchPushError(
            "ERROR_GITHUB_PUSH_REPO_INVALID",
            "remote_repo must be owner/name.",
        )
    remote_repo = remote_repo.strip()
    if not isinstance(expected_head_sha, str) or not SHA40.fullmatch(expected_head_sha):
        raise GitHubBranchPushError(
            "ERROR_GITHUB_PUSH_HEAD_INVALID",
            "expected_head_sha must be a lowercase 40-character SHA.",
        )
    if not isinstance(expected_request_sha256, str) or not SHA256.fullmatch(expected_request_sha256):
        raise GitHubBranchPushError(
            "ERROR_GITHUB_PUSH_REQUEST_HASH_INVALID",
            "expected_request_sha256 must be a lowercase 64-character SHA-256.",
        )
    if not isinstance(body, str):
        raise GitHubBranchPushError(
            "ERROR_GITHUB_PUSH_BODY_INVALID",
            "body must be a string.",
        )

    try:
        request = build_pull_request_request(
            repo_root,
            expected_head_sha=expected_head_sha,
            head_branch=head_branch,
            base_branch=base_branch,
            title=title,
            body=body,
            cfg=cfg,
        )
    except CodingLifecycleError as exc:
        raise GitHubBranchPushError(exc.status, exc.detail) from exc

    observed_request_sha256 = request.get("request_sha256")
    if observed_request_sha256 != expected_request_sha256:
        raise GitHubBranchPushError(
            "ERROR_GITHUB_PUSH_REQUEST_DRIFT",
            f"expected request {expected_request_sha256}, observed {observed_request_sha256}",
        )

    try:
        origin_repo = _local_origin_repo(str(request["repo_root"]))
    except GitHubPullRequestError as exc:
        raise GitHubBranchPushError(exc.status, exc.detail) from exc
    if origin_repo != remote_repo:
        raise GitHubBranchPushError(
            "ERROR_GITHUB_PUSH_ORIGIN_MISMATCH",
            f"local origin resolves to {origin_repo}, requested remote is {remote_repo}",
        )

    try:
        before = inspect_repository(str(request["repo_root"]), cfg=cfg)
    except CodingWorkspaceError as exc:
        raise GitHubBranchPushError(exc.status, exc.detail) from exc
    if (
        before.get("head_sha") != expected_head_sha
        or before.get("branch") != head_branch
        or before.get("dirty_status") != "CLEAN"
    ):
        raise GitHubBranchPushError(
            "ERROR_GITHUB_PUSH_LOCAL_STATE_DRIFT",
            "local branch, HEAD, or clean-state changed after PR request validation.",
        )

    refspec = f"{expected_head_sha}:refs/heads/{head_branch}"
    dry_run = _git_push(
        str(request["repo_root"]),
        refspec=refspec,
        dry_run=True,
    )
    if dry_run.returncode != 0:
        detail = (dry_run.stderr or dry_run.stdout or "git push --dry-run failed").strip()
        raise GitHubBranchPushError(
            "ERROR_GITHUB_PUSH_DRY_RUN_REJECTED",
            detail[:4000],
        )

    pushed = _git_push(
        str(request["repo_root"]),
        refspec=refspec,
        dry_run=False,
    )
    if pushed.returncode != 0:
        detail = (pushed.stderr or pushed.stdout or "git push failed").strip()
        raise GitHubBranchPushError(
            "ERROR_GITHUB_PUSH_REJECTED",
            detail[:4000],
        )

    try:
        remote_head_sha = _remote_branch_sha(remote_repo, head_branch)
    except GitHubPullRequestError as exc:
        raise GitHubBranchPushError(exc.status, exc.detail) from exc
    if remote_head_sha != expected_head_sha:
        raise GitHubBranchPushError(
            "ERROR_GITHUB_PUSH_POSTCONDITION_HEAD",
            f"remote {head_branch} is {remote_head_sha}, expected {expected_head_sha}",
        )

    try:
        after = inspect_repository(str(request["repo_root"]), cfg=cfg)
    except CodingWorkspaceError as exc:
        raise GitHubBranchPushError(exc.status, exc.detail) from exc
    if (
        after.get("head_sha") != expected_head_sha
        or after.get("branch") != head_branch
        or after.get("dirty_status") != "CLEAN"
    ):
        raise GitHubBranchPushError(
            "ERROR_GITHUB_PUSH_LOCAL_POSTCONDITION",
            "remote push changed or coincided with unexpected local repository state.",
        )

    return {
        "status": "GITHUB_BRANCH_PUSH_OK",
        "retrieved_utc": utc_now(),
        "remote_repo": remote_repo,
        "local_origin_repo": origin_repo,
        "request_sha256": expected_request_sha256,
        "head_branch": head_branch,
        "base_branch": base_branch,
        "local_head_sha": expected_head_sha,
        "remote_head_sha": remote_head_sha,
        "refspec": refspec,
        "dry_run": _output_digest(dry_run),
        "push": _output_digest(pushed),
        "network_action_performed": True,
        "force_used": False,
        "local_state_after": {
            "head_sha": after.get("head_sha"),
            "branch": after.get("branch"),
            "dirty_status": after.get("dirty_status"),
        },
        "invariants": {
            "local_request_exact": observed_request_sha256 == expected_request_sha256,
            "origin_repo_exact": origin_repo == remote_repo,
            "local_head_exact": after.get("head_sha") == expected_head_sha,
            "local_branch_exact": after.get("branch") == head_branch,
            "local_clean": after.get("dirty_status") == "CLEAN",
            "remote_head_exact": remote_head_sha == expected_head_sha,
            "force_disabled": True,
            "dry_run_passed": dry_run.returncode == 0,
        },
        "limitations": [
            "Authentication is supplied by the operator's configured Git transport; this adapter does not persist credentials.",
            "Only origin is used; arbitrary remote names and arbitrary refspecs are not accepted.",
            "No force, force-with-lease, tag, delete, or protected-head operation is exposed.",
        ],
        "falsification_criteria": [
            "reject PR request hash drift",
            "reject local origin repository mismatch",
            "reject local branch, HEAD, or dirty-state drift",
            "reject a dry-run non-fast-forward or authentication failure",
            "reject a real push failure",
            "re-read the remote branch and reject any post-push SHA mismatch",
        ],
    }
