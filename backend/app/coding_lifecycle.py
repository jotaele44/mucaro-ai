from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from .coding_workspace import CodingWorkspaceError, inspect_repository, resolve_repository
from .config import Settings, settings

CODING_LIFECYCLE_SCHEMA_VERSION = "mucaro-coding-lifecycle/1.0"
PROTECTED_BRANCHES = {"main", "master"}
MAX_BRANCH_BYTES = 240
MAX_COMMIT_MESSAGE_BYTES = 16 * 1024
MAX_PR_TITLE_BYTES = 512
MAX_PR_BODY_BYTES = 128 * 1024


class CodingLifecycleError(RuntimeError):
    def __init__(self, status: str, detail: str, *, receipt: dict[str, Any] | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.receipt = receipt


def _git(repo_root: Path, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise CodingLifecycleError("ERROR_CODING_GIT_UNAVAILABLE", "git executable is not available") from exc
    except subprocess.TimeoutExpired as exc:
        raise CodingLifecycleError("ERROR_CODING_LIFECYCLE_TIMEOUT", "git command timed out") from exc


def _git_ok(repo_root: Path, *args: str) -> str:
    proc = _git(repo_root, *args)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "git command failed"
        raise CodingLifecycleError("ERROR_CODING_LIFECYCLE_GIT", detail)
    return proc.stdout


def _validate_branch_name(repo_root: Path, branch: str, *, allow_protected: bool = False) -> str:
    if not isinstance(branch, str) or not branch or len(branch.encode("utf-8")) > MAX_BRANCH_BYTES:
        raise CodingLifecycleError("ERROR_CODING_BRANCH_INVALID", "branch name is empty or exceeds the byte ceiling")
    proc = _git(repo_root, "check-ref-format", "--branch", branch)
    if proc.returncode != 0:
        raise CodingLifecycleError("ERROR_CODING_BRANCH_INVALID", proc.stderr.strip() or branch)
    if not allow_protected and branch in PROTECTED_BRANCHES:
        raise CodingLifecycleError("ERROR_CODING_PROTECTED_BRANCH", branch)
    return branch


def _bounded_text(value: str, *, label: str, max_bytes: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CodingLifecycleError(f"ERROR_CODING_{label}_REQUIRED", f"{label.lower()} must be non-empty")
    normalized = value.strip()
    if len(normalized.encode("utf-8")) > max_bytes:
        raise CodingLifecycleError(f"ERROR_CODING_{label}_TOO_LARGE", f"{label.lower()} exceeds {max_bytes} bytes")
    return normalized


def _validate_paths(paths: list[str]) -> list[str]:
    if not isinstance(paths, list) or not paths:
        raise CodingLifecycleError("ERROR_CODING_COMMIT_PATHS_REQUIRED", "approved_paths must be a non-empty list")
    result: set[str] = set()
    for path in paths:
        if not isinstance(path, str) or not path or path.startswith("/"):
            raise CodingLifecycleError("ERROR_CODING_COMMIT_PATH_INVALID", str(path))
        parts = Path(path).parts
        if ".." in parts or (parts and parts[0] == ".git"):
            raise CodingLifecycleError("ERROR_CODING_COMMIT_PATH_INVALID", path)
        result.add(Path(path).as_posix())
    return sorted(result)


def _dirty_digest(paths: list[str]) -> str:
    return hashlib.sha256(json.dumps(sorted(paths), separators=(",", ":")).encode("utf-8")).hexdigest()


def _receipt_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def create_work_branch_transaction(
    repo_root: str | Path,
    *,
    branch_name: str,
    expected_head_sha: str,
    cfg: Settings = settings,
) -> dict[str, Any]:
    try:
        context = resolve_repository(repo_root, cfg)
        before = inspect_repository(context.repo_root, cfg=cfg)
    except CodingWorkspaceError as exc:
        raise CodingLifecycleError(exc.status, exc.detail) from exc
    branch_name = _validate_branch_name(context.repo_root, branch_name)
    if before["head_sha"] != expected_head_sha:
        raise CodingLifecycleError("ERROR_CODING_BRANCH_HEAD_DRIFT", f"expected {expected_head_sha}, observed {before['head_sha']}")
    if before["dirty_status"] != "CLEAN":
        raise CodingLifecycleError("ERROR_CODING_BRANCH_DIRTY_REPOSITORY", "work branches must be created from a clean repository")
    exists = _git(context.repo_root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}")
    if exists.returncode == 0:
        raise CodingLifecycleError("ERROR_CODING_BRANCH_EXISTS", branch_name)

    original_branch = before["branch"]
    proc = _git(context.repo_root, "switch", "-c", branch_name, expected_head_sha)
    if proc.returncode != 0:
        raise CodingLifecycleError("ERROR_CODING_BRANCH_CREATE_FAILED", proc.stderr.strip() or branch_name)
    try:
        after = inspect_repository(context.repo_root, cfg=cfg)
    except Exception as exc:
        rollback_args = ("switch", original_branch) if original_branch != "DETACHED" else ("switch", "--detach", expected_head_sha)
        rollback = _git(context.repo_root, *rollback_args)
        raise CodingLifecycleError(
            "ERROR_CODING_BRANCH_VERIFY_FAILED",
            f"branch verification failed; rollback_exit={rollback.returncode}",
        ) from exc

    if after["head_sha"] != expected_head_sha or after["branch"] != branch_name or after["dirty_status"] != "CLEAN":
        rollback_args = ("switch", original_branch) if original_branch != "DETACHED" else ("switch", "--detach", expected_head_sha)
        rollback = _git(context.repo_root, *rollback_args)
        receipt = {
            "schema_version": CODING_LIFECYCLE_SCHEMA_VERSION,
            "status": "CODING_BRANCH_ROLLED_BACK" if rollback.returncode == 0 else "CODING_BRANCH_ROLLBACK_FAILED",
            "requested_branch": branch_name,
            "expected_head_sha": expected_head_sha,
            "observed_after": after,
            "rollback_exit_code": rollback.returncode,
        }
        raise CodingLifecycleError(receipt["status"], "branch postconditions failed", receipt=receipt)

    payload = {
        "schema_version": CODING_LIFECYCLE_SCHEMA_VERSION,
        "status": "CODING_BRANCH_CREATED",
        "repo_root": str(context.repo_root),
        "original_branch": original_branch,
        "branch": branch_name,
        "head_sha": expected_head_sha,
        "dirty_status": "CLEAN",
        "invariants": {
            "head_conserved": True,
            "clean_state_conserved": True,
            "protected_branch_not_created": branch_name not in PROTECTED_BRANCHES,
        },
    }
    payload["receipt_sha256"] = _receipt_hash(payload)
    return payload


def commit_approved_changes(
    repo_root: str | Path,
    *,
    expected_head_sha: str,
    expected_branch: str,
    approved_paths: list[str],
    message: str,
    cfg: Settings = settings,
) -> dict[str, Any]:
    try:
        context = resolve_repository(repo_root, cfg)
        before = inspect_repository(context.repo_root, cfg=cfg)
    except CodingWorkspaceError as exc:
        raise CodingLifecycleError(exc.status, exc.detail) from exc
    expected_branch = _validate_branch_name(context.repo_root, expected_branch)
    approved = _validate_paths(approved_paths)
    message = _bounded_text(message, label="COMMIT_MESSAGE", max_bytes=MAX_COMMIT_MESSAGE_BYTES)

    if before["head_sha"] != expected_head_sha:
        raise CodingLifecycleError("ERROR_CODING_COMMIT_HEAD_DRIFT", f"expected {expected_head_sha}, observed {before['head_sha']}")
    if before["branch"] != expected_branch:
        raise CodingLifecycleError("ERROR_CODING_COMMIT_BRANCH_DRIFT", f"expected {expected_branch}, observed {before['branch']}")
    if before.get("staged_files"):
        raise CodingLifecycleError("ERROR_CODING_COMMIT_PRESTAGED_STATE", "pre-existing staged changes are not accepted by this transaction")
    observed_dirty = sorted(before.get("dirty_paths") or [])
    if observed_dirty != approved:
        raise CodingLifecycleError(
            "ERROR_CODING_COMMIT_DIRTY_SET_DRIFT",
            f"approved={approved} observed={observed_dirty}",
        )

    add = _git(context.repo_root, "add", "-A", "--", *approved)
    if add.returncode != 0:
        raise CodingLifecycleError("ERROR_CODING_COMMIT_STAGE_FAILED", add.stderr.strip() or "git add failed")
    staged_state = inspect_repository(context.repo_root, cfg=cfg)
    staged = sorted(staged_state.get("staged_files") or [])
    if sorted(staged_state.get("dirty_paths") or []) != approved or not staged:
        _git(context.repo_root, "reset", "--mixed", expected_head_sha)
        raise CodingLifecycleError("ERROR_CODING_COMMIT_STAGE_VERIFY_FAILED", "staged candidate set did not match approved dirty set")

    commit = _git(context.repo_root, "commit", "-m", message)
    if commit.returncode != 0:
        rollback = _git(context.repo_root, "reset", "--mixed", expected_head_sha)
        after_rollback = inspect_repository(context.repo_root, cfg=cfg)
        receipt = {
            "schema_version": CODING_LIFECYCLE_SCHEMA_VERSION,
            "status": "CODING_COMMIT_ROLLED_BACK" if rollback.returncode == 0 else "CODING_COMMIT_ROLLBACK_FAILED",
            "head_sha": after_rollback["head_sha"],
            "branch": after_rollback["branch"],
            "dirty_paths": sorted(after_rollback.get("dirty_paths") or []),
            "rollback_exit_code": rollback.returncode,
            "commit_detail": commit.stderr.strip() or commit.stdout.strip() or None,
        }
        raise CodingLifecycleError(receipt["status"], "git commit failed", receipt=receipt)

    after = inspect_repository(context.repo_root, cfg=cfg)
    parent = _git_ok(context.repo_root, "rev-parse", "HEAD^").strip()
    changed = sorted(
        path
        for path in _git_ok(context.repo_root, "diff-tree", "--no-commit-id", "--name-only", "-r", "--no-renames", "HEAD").splitlines()
        if path
    )
    if parent != expected_head_sha or after["branch"] != expected_branch or after["dirty_status"] != "CLEAN" or changed != approved:
        rollback = _git(context.repo_root, "reset", "--mixed", expected_head_sha)
        restored = inspect_repository(context.repo_root, cfg=cfg)
        receipt = {
            "schema_version": CODING_LIFECYCLE_SCHEMA_VERSION,
            "status": "CODING_COMMIT_ROLLED_BACK" if rollback.returncode == 0 else "CODING_COMMIT_ROLLBACK_FAILED",
            "new_commit_sha": after["head_sha"],
            "expected_parent_sha": expected_head_sha,
            "observed_parent_sha": parent,
            "approved_paths": approved,
            "commit_changed_paths": changed,
            "restored_head_sha": restored["head_sha"],
            "restored_dirty_paths": sorted(restored.get("dirty_paths") or []),
        }
        raise CodingLifecycleError(receipt["status"], "commit postconditions failed", receipt=receipt)

    payload = {
        "schema_version": CODING_LIFECYCLE_SCHEMA_VERSION,
        "status": "CODING_COMMIT_CREATED",
        "repo_root": str(context.repo_root),
        "branch": expected_branch,
        "parent_sha": expected_head_sha,
        "commit_sha": after["head_sha"],
        "approved_paths": approved,
        "approved_paths_sha256": _dirty_digest(approved),
        "commit_changed_paths": changed,
        "commit_message_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest(),
        "dirty_status_after": after["dirty_status"],
        "invariants": {
            "parent_exact": parent == expected_head_sha,
            "branch_exact": after["branch"] == expected_branch,
            "candidate_set_exact": changed == approved,
            "clean_after_commit": after["dirty_status"] == "CLEAN",
            "protected_branch_not_mutated": expected_branch not in PROTECTED_BRANCHES,
        },
    }
    payload["receipt_sha256"] = _receipt_hash(payload)
    return payload


def build_pull_request_request(
    repo_root: str | Path,
    *,
    expected_head_sha: str,
    head_branch: str,
    base_branch: str,
    title: str,
    body: str,
    cfg: Settings = settings,
) -> dict[str, Any]:
    try:
        context = resolve_repository(repo_root, cfg)
        state = inspect_repository(context.repo_root, cfg=cfg)
    except CodingWorkspaceError as exc:
        raise CodingLifecycleError(exc.status, exc.detail) from exc
    head_branch = _validate_branch_name(context.repo_root, head_branch)
    base_branch = _validate_branch_name(context.repo_root, base_branch, allow_protected=True)
    title = _bounded_text(title, label="PR_TITLE", max_bytes=MAX_PR_TITLE_BYTES)
    if not isinstance(body, str) or len(body.encode("utf-8")) > MAX_PR_BODY_BYTES:
        raise CodingLifecycleError("ERROR_CODING_PR_BODY_TOO_LARGE", f"PR body exceeds {MAX_PR_BODY_BYTES} bytes")
    if head_branch == base_branch:
        raise CodingLifecycleError("ERROR_CODING_PR_SAME_BRANCH", head_branch)
    if state["head_sha"] != expected_head_sha:
        raise CodingLifecycleError("ERROR_CODING_PR_HEAD_DRIFT", f"expected {expected_head_sha}, observed {state['head_sha']}")
    if state["branch"] != head_branch:
        raise CodingLifecycleError("ERROR_CODING_PR_BRANCH_DRIFT", f"expected {head_branch}, observed {state['branch']}")
    if state["dirty_status"] != "CLEAN":
        raise CodingLifecycleError("ERROR_CODING_PR_DIRTY_REPOSITORY", "PR requests require a clean committed work branch")

    payload = {
        "schema_version": CODING_LIFECYCLE_SCHEMA_VERSION,
        "status": "CODING_PR_REQUEST_READY",
        "repo_root": str(context.repo_root),
        "head_branch": head_branch,
        "head_sha": expected_head_sha,
        "base_branch": base_branch,
        "title": title,
        "body": body,
        "network_action_performed": False,
        "requires_authenticated_remote_adapter": True,
        "invariants": {
            "head_exact": True,
            "branch_exact": True,
            "repository_clean": True,
            "head_branch_not_protected": head_branch not in PROTECTED_BRANCHES,
            "head_base_distinct": head_branch != base_branch,
        },
        "limitations": [
            "this function emits the exact governed PR request artifact but does not call a remote Git hosting API",
            "remote authentication and credential provisioning remain a separate final-stage capability",
        ],
    }
    payload["request_sha256"] = _receipt_hash(payload)
    return payload
