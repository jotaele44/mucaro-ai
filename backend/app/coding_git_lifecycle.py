from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .coding_workspace import CodingWorkspaceError, inspect_repository, resolve_repository
from .config import Settings, settings

GIT_LIFECYCLE_SCHEMA_VERSION = "mucaro-coding-git-lifecycle/1.1"
PROTECTED_BRANCH_NAMES = {"main", "master"}
MAX_BRANCH_BYTES = 200
MAX_COMMIT_MESSAGE_BYTES = 16 * 1024
MAX_STAGED_DIFF_BYTES = 8 * 1024 * 1024


class CodingGitLifecycleError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class CodingGitLifecyclePlan:
    repo_root: Path
    source_branch: str
    base_head_sha: str
    target_branch: str
    commit_message: str
    staged_paths: tuple[str, ...]
    staged_paths_sha256: str
    staged_diff_sha256: str
    staged_diff_bytes: int
    pr_base_branch: str

    @property
    def plan_sha256(self) -> str:
        payload = {
            "schema_version": GIT_LIFECYCLE_SCHEMA_VERSION,
            "repo_root": str(self.repo_root),
            "source_branch": self.source_branch,
            "base_head_sha": self.base_head_sha,
            "target_branch": self.target_branch,
            "commit_message": self.commit_message,
            "staged_paths": list(self.staged_paths),
            "staged_paths_sha256": self.staged_paths_sha256,
            "staged_diff_sha256": self.staged_diff_sha256,
            "staged_diff_bytes": self.staged_diff_bytes,
            "pr_base_branch": self.pr_base_branch,
            "force_allowed": False,
            "remote_mutation_allowed": False,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _git(repo: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *argv], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30
        )
    except FileNotFoundError as exc:
        raise CodingGitLifecycleError("ERROR_CODING_GIT_UNAVAILABLE", "git executable is not available") from exc
    except subprocess.TimeoutExpired as exc:
        raise CodingGitLifecycleError("ERROR_CODING_GIT_TIMEOUT", "git command timed out") from exc


def _git_bytes(repo: Path, *argv: str) -> bytes:
    try:
        proc = subprocess.run(
            ["git", *argv], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30
        )
    except FileNotFoundError as exc:
        raise CodingGitLifecycleError("ERROR_CODING_GIT_UNAVAILABLE", "git executable is not available") from exc
    except subprocess.TimeoutExpired as exc:
        raise CodingGitLifecycleError("ERROR_CODING_GIT_TIMEOUT", "git command timed out") from exc
    if proc.returncode != 0:
        raise CodingGitLifecycleError(
            "ERROR_CODING_GIT_STAGED_DIFF", proc.stderr.decode("utf-8", errors="replace").strip() or "git diff failed"
        )
    if len(proc.stdout) > MAX_STAGED_DIFF_BYTES:
        raise CodingGitLifecycleError(
            "ERROR_CODING_GIT_STAGED_DIFF_TOO_LARGE", f"staged diff exceeds {MAX_STAGED_DIFF_BYTES} bytes"
        )
    return proc.stdout


def _validate_branch(repo: Path, branch: str) -> str:
    if not isinstance(branch, str) or not branch or len(branch.encode("utf-8")) > MAX_BRANCH_BYTES:
        raise CodingGitLifecycleError("ERROR_CODING_GIT_BRANCH_INVALID", "branch name is empty or exceeds the byte ceiling")
    if branch in PROTECTED_BRANCH_NAMES:
        raise CodingGitLifecycleError("ERROR_CODING_GIT_PROTECTED_BRANCH", branch)
    proc = _git(repo, "check-ref-format", "--branch", branch)
    if proc.returncode != 0:
        raise CodingGitLifecycleError("ERROR_CODING_GIT_BRANCH_INVALID", proc.stderr.strip() or branch)
    exists = _git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}")
    if exists.returncode == 0:
        raise CodingGitLifecycleError("ERROR_CODING_GIT_BRANCH_EXISTS", branch)
    if exists.returncode not in {0, 1}:
        raise CodingGitLifecycleError("ERROR_CODING_GIT_REF_CHECK", exists.stderr.strip() or branch)
    return branch


def _validate_commit_message(message: str) -> str:
    if not isinstance(message, str) or not message.strip():
        raise CodingGitLifecycleError("ERROR_CODING_GIT_COMMIT_MESSAGE", "commit message is required")
    if "\x00" in message or len(message.encode("utf-8")) > MAX_COMMIT_MESSAGE_BYTES:
        raise CodingGitLifecycleError("ERROR_CODING_GIT_COMMIT_MESSAGE", "commit message is invalid or exceeds the byte ceiling")
    return message


def _digest_paths(paths: list[str]) -> str:
    return hashlib.sha256(json.dumps(sorted(paths), separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _staged_diff_identity(repo: Path) -> tuple[str, int]:
    raw = _git_bytes(repo, "diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv")
    if not raw:
        raise CodingGitLifecycleError("ERROR_CODING_GIT_NOTHING_STAGED", "staged diff is empty")
    return hashlib.sha256(raw).hexdigest(), len(raw)


def build_git_lifecycle_plan(
    repo_root: str | Path,
    *,
    target_branch: str,
    commit_message: str,
    expected_head_sha: str,
    pr_base_branch: str = "main",
    cfg: Settings = settings,
) -> CodingGitLifecyclePlan:
    try:
        context = resolve_repository(repo_root, cfg)
        state = inspect_repository(context.repo_root, cfg=cfg)
    except CodingWorkspaceError as exc:
        raise CodingGitLifecycleError(exc.status, exc.detail) from exc
    if state["head_sha"] != expected_head_sha:
        raise CodingGitLifecycleError(
            "ERROR_CODING_GIT_HEAD_DRIFT", f"expected {expected_head_sha}, observed {state['head_sha']}"
        )
    source_branch = str(state.get("branch") or "")
    if not source_branch or source_branch == "DETACHED":
        raise CodingGitLifecycleError("ERROR_CODING_GIT_DETACHED_HEAD", "branch lifecycle requires an attached source branch")
    target = _validate_branch(context.repo_root, target_branch)
    message = _validate_commit_message(commit_message)
    staged_paths = sorted(str(path) for path in state.get("staged_paths") or [])
    if not staged_paths:
        raise CodingGitLifecycleError("ERROR_CODING_GIT_NOTHING_STAGED", "at least one staged path is required")
    staged_diff_sha256, staged_diff_bytes = _staged_diff_identity(context.repo_root)
    base = pr_base_branch.strip() if isinstance(pr_base_branch, str) else ""
    if not base or base == target:
        raise CodingGitLifecycleError("ERROR_CODING_GIT_PR_BASE_INVALID", "PR base must be non-empty and differ from target branch")
    base_check = _git(context.repo_root, "rev-parse", "--verify", f"refs/heads/{base}^{{commit}}")
    if base_check.returncode != 0:
        raise CodingGitLifecycleError("ERROR_CODING_GIT_PR_BASE_MISSING", base)
    return CodingGitLifecyclePlan(
        repo_root=context.repo_root,
        source_branch=source_branch,
        base_head_sha=expected_head_sha,
        target_branch=target,
        commit_message=message,
        staged_paths=tuple(staged_paths),
        staged_paths_sha256=_digest_paths(staged_paths),
        staged_diff_sha256=staged_diff_sha256,
        staged_diff_bytes=staged_diff_bytes,
        pr_base_branch=base,
    )


def validate_git_lifecycle_plan(plan: CodingGitLifecyclePlan, *, cfg: Settings = settings) -> dict[str, Any]:
    try:
        state = inspect_repository(plan.repo_root, cfg=cfg)
    except CodingWorkspaceError as exc:
        raise CodingGitLifecycleError(exc.status, exc.detail) from exc
    current_staged = sorted(str(path) for path in state.get("staged_paths") or [])
    head_match = state["head_sha"] == plan.base_head_sha
    branch_match = state.get("branch") == plan.source_branch
    staged_paths_match = _digest_paths(current_staged) == plan.staged_paths_sha256 and tuple(current_staged) == plan.staged_paths
    try:
        current_diff_sha256, current_diff_bytes = _staged_diff_identity(plan.repo_root)
        staged_bytes_match = current_diff_sha256 == plan.staged_diff_sha256 and current_diff_bytes == plan.staged_diff_bytes
    except CodingGitLifecycleError as exc:
        if exc.status != "ERROR_CODING_GIT_NOTHING_STAGED":
            raise
        current_diff_sha256 = None
        current_diff_bytes = 0
        staged_bytes_match = False
    target_probe = _git(plan.repo_root, "show-ref", "--verify", "--quiet", f"refs/heads/{plan.target_branch}")
    if target_probe.returncode not in {0, 1}:
        raise CodingGitLifecycleError("ERROR_CODING_GIT_REF_CHECK", target_probe.stderr.strip() or plan.target_branch)
    target_absent = target_probe.returncode == 1
    valid = head_match and branch_match and staged_paths_match and staged_bytes_match and target_absent
    payload = {
        "schema_version": GIT_LIFECYCLE_SCHEMA_VERSION,
        "status": "CODING_GIT_LIFECYCLE_PLAN_VALID" if valid else "CODING_GIT_LIFECYCLE_PLAN_STALE",
        "plan_sha256": plan.plan_sha256,
        "base_head_sha": plan.base_head_sha,
        "source_branch": plan.source_branch,
        "target_branch": plan.target_branch,
        "pr_base_branch": plan.pr_base_branch,
        "staged_paths": list(plan.staged_paths),
        "staged_paths_sha256": plan.staged_paths_sha256,
        "staged_diff_sha256": plan.staged_diff_sha256,
        "staged_diff_bytes": plan.staged_diff_bytes,
        "current_staged_diff_sha256": current_diff_sha256,
        "current_staged_diff_bytes": current_diff_bytes,
        "head_match": head_match,
        "source_branch_match": branch_match,
        "staged_paths_match": staged_paths_match,
        "staged_bytes_match": staged_bytes_match,
        "target_branch_absent": target_absent,
        "branch_create_argv": ["git", "switch", "-c", plan.target_branch, plan.base_head_sha],
        "commit_argv": ["git", "commit", "-m", plan.commit_message, "--"],
        "pr_plan": {
            "head": plan.target_branch,
            "base": plan.pr_base_branch,
            "expected_source_head_sha": plan.base_head_sha,
            "remote_action": "NOT_EXECUTED",
        },
        "force_allowed": False,
        "push_allowed": False,
        "remote_mutation_allowed": False,
        "limitations": [
            "this artifact plans local branch/commit and remote PR identity but performs no Git or remote mutation",
            "local commit hooks remain in force because the plan does not bypass them",
            "remote push/PR creation remains blocked until an authorized connector and explicit approval are present",
        ],
        "falsification_criteria": [
            "invalidate on HEAD, source-branch, staged-path or staged-byte drift",
            "reject protected or already-existing target branches",
            "never enable force push from this plan",
            "never infer PR creation from a local branch or commit",
        ],
    }
    payload["receipt_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return payload
