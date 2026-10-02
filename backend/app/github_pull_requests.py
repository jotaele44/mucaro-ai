from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from .coding_lifecycle import CodingLifecycleError, build_pull_request_request
from .config import Settings, settings
from .local_tools import resolve_local_tool

REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
MAX_TOKEN_CHARS = 240


class GitHubPullRequestError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _bounded_token(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > MAX_TOKEN_CHARS:
        raise GitHubPullRequestError(f"ERROR_GITHUB_PR_{label.upper()}_INVALID", f"{label} is invalid")
    cleaned = value.strip()
    if any(char in cleaned for char in ("\x00", "\n", "\r")):
        raise GitHubPullRequestError(f"ERROR_GITHUB_PR_{label.upper()}_INVALID", f"{label} contains a control character")
    return cleaned


def _gh(args: list[str], *, timeout_seconds: int = 45, input_text: str | None = None) -> str:
    try:
        gh = resolve_local_tool("gh")
    except ValueError as exc:
        raise GitHubPullRequestError("ERROR_GITHUB_PR_GH_UNAVAILABLE", "GitHub CLI is not allowlisted") from exc
    if not gh:
        raise GitHubPullRequestError("ERROR_GITHUB_PR_GH_UNAVAILABLE", "GitHub CLI is not available in this runtime")
    try:
        proc = subprocess.run(
            [gh, *args],
            text=True,
            input=input_text,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitHubPullRequestError("ERROR_GITHUB_PR_TIMEOUT", f"gh command exceeded {timeout_seconds}s") from exc
    except OSError as exc:
        raise GitHubPullRequestError("ERROR_GITHUB_PR_GH_UNAVAILABLE", "GitHub CLI could not be started") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "gh command failed").strip()
        raise GitHubPullRequestError("ERROR_GITHUB_PR_REMOTE", detail)
    return proc.stdout


def _gh_json(args: list[str], *, timeout_seconds: int = 45) -> Any:
    raw = _gh(args, timeout_seconds=timeout_seconds)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GitHubPullRequestError("ERROR_GITHUB_PR_JSON", f"GitHub CLI returned invalid JSON: {exc}") from exc



def _local_origin_repo(repo_root: str) -> str:
    try:
        git = resolve_local_tool("git")
    except ValueError as exc:
        raise GitHubPullRequestError("ERROR_GITHUB_PR_GIT_UNAVAILABLE", "Git is not allowlisted") from exc
    if not git:
        raise GitHubPullRequestError("ERROR_GITHUB_PR_GIT_UNAVAILABLE", "Git is not available in this runtime")
    try:
        proc = subprocess.run(
            [git, "-C", repo_root, "remote", "get-url", "origin"],
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitHubPullRequestError("ERROR_GITHUB_PR_ORIGIN_UNAVAILABLE", "local origin could not be resolved") from exc
    if proc.returncode != 0:
        raise GitHubPullRequestError(
            "ERROR_GITHUB_PR_ORIGIN_UNAVAILABLE",
            (proc.stderr or proc.stdout or "origin is not configured").strip(),
        )
    value = proc.stdout.strip()
    if value.startswith("git@github.com:"):
        path = value.split(":", 1)[1]
    else:
        parsed = urlparse(value)
        if parsed.hostname != "github.com":
            raise GitHubPullRequestError("ERROR_GITHUB_PR_ORIGIN_NOT_GITHUB", "origin is not a github.com repository")
        path = parsed.path.lstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = [part for part in path.split("/") if part]
    if len(parts) != 2:
        raise GitHubPullRequestError("ERROR_GITHUB_PR_ORIGIN_INVALID", "origin does not resolve to owner/name")
    origin_repo = f"{parts[0]}/{parts[1]}"
    if not REPO_PATTERN.fullmatch(origin_repo):
        raise GitHubPullRequestError("ERROR_GITHUB_PR_ORIGIN_INVALID", "origin owner/name is invalid")
    return origin_repo

def _remote_branch_sha(repo: str, branch: str) -> str:
    payload = _gh_json(["api", f"repos/{repo}/git/ref/heads/{branch}"])
    if not isinstance(payload, dict):
        raise GitHubPullRequestError("ERROR_GITHUB_PR_REMOTE_REF_SHAPE", "remote branch lookup was not an object")
    obj = payload.get("object")
    sha = obj.get("sha") if isinstance(obj, dict) else None
    if not isinstance(sha, str) or not SHA_PATTERN.fullmatch(sha):
        raise GitHubPullRequestError("ERROR_GITHUB_PR_REMOTE_REF_SHAPE", "remote branch lookup did not contain an exact commit SHA")
    return sha


def _open_prs(repo: str, *, head_branch: str, base_branch: str) -> list[dict[str, Any]]:
    payload = _gh_json(
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--head",
            head_branch,
            "--state",
            "open",
            "--json",
            "number,url,title,body,headRefName,headRefOid,baseRefName,isDraft",
        ]
    )
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise GitHubPullRequestError("ERROR_GITHUB_PR_LIST_SHAPE", "pull-request listing did not return an array")
    if len(payload) > 1:
        raise GitHubPullRequestError(
            "ERROR_GITHUB_PR_AMBIGUOUS",
            f"multiple open pull requests exist for head branch {head_branch}; base identity is ambiguous",
        )
    return payload


def _view_pr(repo: str, number: int) -> dict[str, Any]:
    payload = _gh_json(
        [
            "pr",
            "view",
            str(number),
            "--repo",
            repo,
            "--json",
            "number,url,title,body,headRefName,headRefOid,baseRefName,isDraft,state",
        ]
    )
    if not isinstance(payload, dict):
        raise GitHubPullRequestError("ERROR_GITHUB_PR_VIEW_SHAPE", "pull-request view did not return an object")
    return payload


def open_or_update_pull_request(
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
    remote_repo = _bounded_token(remote_repo, label="repo")
    if not REPO_PATTERN.fullmatch(remote_repo):
        raise GitHubPullRequestError("ERROR_GITHUB_PR_REPO_INVALID", "remote_repo must be owner/name")
    expected_head_sha = _bounded_token(expected_head_sha, label="head")
    if not SHA_PATTERN.fullmatch(expected_head_sha):
        raise GitHubPullRequestError("ERROR_GITHUB_PR_HEAD_INVALID", "expected_head_sha must be a lowercase 40-character SHA")
    head_branch = _bounded_token(head_branch, label="head_branch")
    base_branch = _bounded_token(base_branch, label="base_branch")
    if not isinstance(expected_request_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_request_sha256):
        raise GitHubPullRequestError("ERROR_GITHUB_PR_REQUEST_HASH_INVALID", "expected_request_sha256 must be a lowercase 64-character SHA-256")

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
        raise GitHubPullRequestError(exc.status, exc.detail) from exc

    observed_request_sha256 = request.get("request_sha256")
    if observed_request_sha256 != expected_request_sha256:
        raise GitHubPullRequestError(
            "ERROR_GITHUB_PR_REQUEST_DRIFT",
            f"expected request {expected_request_sha256}, observed {observed_request_sha256}",
        )

    origin_repo = _local_origin_repo(str(request["repo_root"]))
    if origin_repo != remote_repo:
        raise GitHubPullRequestError(
            "ERROR_GITHUB_PR_ORIGIN_MISMATCH",
            f"local origin resolves to {origin_repo}, requested remote is {remote_repo}",
        )

    remote_head_sha = _remote_branch_sha(remote_repo, head_branch)
    if remote_head_sha != expected_head_sha:
        raise GitHubPullRequestError(
            "ERROR_GITHUB_PR_REMOTE_HEAD_DRIFT",
            f"expected remote {head_branch} at {expected_head_sha}, observed {remote_head_sha}",
        )

    existing = _open_prs(remote_repo, head_branch=head_branch, base_branch=base_branch)
    network_action_performed = False
    action: str
    if not existing:
        raw = _gh(
            [
                "pr",
                "create",
                "--repo",
                remote_repo,
                "--head",
                head_branch,
                "--base",
                base_branch,
                "--title",
                request["title"],
                "--body-file",
                "-",
            ],
            timeout_seconds=60,
            input_text=request["body"],
        ).strip()
        created = _open_prs(remote_repo, head_branch=head_branch, base_branch=base_branch)
        if len(created) != 1:
            raise GitHubPullRequestError(
                "ERROR_GITHUB_PR_CREATE_VERIFY",
                f"create returned {raw or '<empty>'} but exact open PR could not be resolved",
            )
        pr = created[0]
        action = "CREATED"
        network_action_performed = True
    else:
        pr = existing[0]
        if pr.get("headRefOid") != expected_head_sha:
            raise GitHubPullRequestError(
                "ERROR_GITHUB_PR_EXISTING_HEAD_DRIFT",
                f"open PR head is {pr.get('headRefOid')}, expected {expected_head_sha}",
            )
        if pr.get("headRefName") != head_branch:
            raise GitHubPullRequestError("ERROR_GITHUB_PR_EXISTING_IDENTITY", "open PR head branch identity does not match the approved request")
        number = pr.get("number")
        if not isinstance(number, int):
            raise GitHubPullRequestError("ERROR_GITHUB_PR_EXISTING_IDENTITY", "open PR has no numeric identity")
        if (
            pr.get("title") == request["title"]
            and (pr.get("body") or "") == request["body"]
            and pr.get("baseRefName") == base_branch
        ):
            action = "UNCHANGED"
        else:
            _gh(
                [
                    "pr",
                    "edit",
                    str(number),
                    "--repo",
                    remote_repo,
                    "--title",
                    request["title"],
                    "--body-file",
                    "-",
                    "--base",
                    base_branch,
                ],
                timeout_seconds=60,
                input_text=request["body"],
            )
            action = "UPDATED"
            network_action_performed = True
        pr = _view_pr(remote_repo, number)

    if pr.get("headRefOid") != expected_head_sha:
        raise GitHubPullRequestError(
            "ERROR_GITHUB_PR_POSTCONDITION_HEAD",
            f"resulting PR head is {pr.get('headRefOid')}, expected {expected_head_sha}",
        )
    if pr.get("headRefName") != head_branch or pr.get("baseRefName") != base_branch:
        raise GitHubPullRequestError("ERROR_GITHUB_PR_POSTCONDITION_IDENTITY", "resulting PR branch identity changed")

    return {
        "status": "GITHUB_PR_OPEN_UPDATE_OK",
        "action": action,
        "retrieved_utc": _utc_now(),
        "remote_repo": remote_repo,
        "local_origin_repo": origin_repo,
        "request_sha256": expected_request_sha256,
        "local_head_sha": expected_head_sha,
        "remote_head_sha": remote_head_sha,
        "head_branch": head_branch,
        "base_branch": base_branch,
        "pull_request": {
            "number": pr.get("number"),
            "url": pr.get("url"),
            "title": pr.get("title"),
            "body": pr.get("body"),
            "state": pr.get("state") or "OPEN",
            "draft": pr.get("isDraft"),
            "head_sha": pr.get("headRefOid"),
        },
        "network_action_performed": network_action_performed,
        "invariants": {
            "local_request_exact": observed_request_sha256 == expected_request_sha256,
            "origin_repo_exact": origin_repo == remote_repo,
            "remote_head_exact": remote_head_sha == expected_head_sha,
            "pr_head_exact": pr.get("headRefOid") == expected_head_sha,
            "pr_head_branch_exact": pr.get("headRefName") == head_branch,
            "pr_base_branch_exact": pr.get("baseRefName") == base_branch,
        },
        "limitations": [
            "This adapter does not push branches; the approved head branch must already exist remotely at the exact local commit SHA.",
            "The adapter operates only on the unique open PR for the exact head/base branch pair and fails closed on ambiguity.",
            "GitHub authentication remains credential-last and is provided by the operator's authenticated GitHub CLI session.",
        ],
        "falsification_criteria": [
            "reject local request hash drift",
            "reject local origin repository mismatch",
            "reject local or remote head SHA drift",
            "reject multiple open PR candidates for the same head/base pair",
            "verify branch identity and head SHA again after every create/update action",
        ],
    }
