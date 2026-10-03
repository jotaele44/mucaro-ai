"""Authenticated remote push and pull-request submission, bound to the preserved PR request artifact.

docs/CODING_GOVERNANCE_QUEUE.md: remote submission is enabled only after the local branch, commit and
PR-request gates pass, is bound to the exact request SHA-256, and preserves the resulting remote receipt.
Credentials stay with the operator's own `git` and `gh` logins: Mucaro never reads, stores or logs a token.
Push and PR creation are separate steps so each can be approved, audited and verified on its own.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .coding_lifecycle import (
    CODING_LIFECYCLE_SCHEMA_VERSION,
    CodingLifecycleError,
    _receipt_hash,
    build_pull_request_request,
)
from .coding_workspace import CodingWorkspaceError, resolve_repository
from .config import Settings, settings

CODING_REMOTE_SCHEMA_VERSION = "mucaro-coding-remote/1.0"
REMOTE_NAME = "origin"
GIT_TIMEOUT_SECONDS = 30
LS_REMOTE_TIMEOUT_SECONDS = 60
PUSH_TIMEOUT_SECONDS = 180
GH_TIMEOUT_SECONDS = 60
MAX_REQUEST_BYTES = 1_000_000
MAX_DIAGNOSTIC_CHARS = 400
SLUG = r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9._-]{1,100}"
# Plain github.com remotes only: no other host, and no credentials embedded in the URL.
GITHUB_REMOTE_URL = re.compile(rf"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)(?P<slug>{SLUG}?)(?:\.git)?/?")
PR_URL = re.compile(rf"https://github\.com/(?P<slug>{SLUG})/pull/(?P<number>\d+)")
SHA1 = re.compile(r"[0-9a-f]{40}")
TOKEN_SHAPES = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
# git and gh get the few variables they need to find the operator's own login, nothing else from the environment.
PASSTHROUGH_ENV = (
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "SSH_AUTH_SOCK",
    "GH_TOKEN", "GITHUB_TOKEN", "GH_HOST", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "ALL_PROXY",
)
FORCED_ENV = {"GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1", "NO_COLOR": "1"}
CREDENTIAL_HANDLING = "delegated to the operator's own git/gh login; Mucaro never reads, stores or logs a token"


class CodingRemoteError(RuntimeError):
    def __init__(self, status: str, detail: str, *, receipt: dict[str, Any] | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.receipt = receipt


def _environment() -> dict[str, str]:
    env = {key: os.environ[key] for key in PASSTHROUGH_ENV if key in os.environ}
    env.update(FORCED_ENV)
    return env


def _tail(raw: bytes) -> str:
    """The end of a tool's output for a diagnostic: bounded, with anything shaped like a GitHub token removed."""
    text = TOKEN_SHAPES.sub("[redacted]", raw.decode("utf-8", errors="replace").strip())
    return text[-MAX_DIAGNOSTIC_CHARS:]


def _run(argv: list[str], *, cwd: Path, timeout: int, stdin: bytes = b"") -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(argv, cwd=cwd, env=_environment(), input=stdin, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise CodingRemoteError("ERROR_CODING_REMOTE_TOOL_UNAVAILABLE", f"{argv[0]} is not available") from exc
    except subprocess.TimeoutExpired as exc:
        raise CodingRemoteError("ERROR_CODING_REMOTE_TIMEOUT", f"{Path(argv[0]).name} {argv[1]} did not finish within {timeout}s") from exc


def verify_request(request: Any) -> dict[str, Any]:
    """The PR request artifact, proven intact: its request_sha256 must match its own content."""
    if not isinstance(request, dict):
        raise CodingRemoteError("ERROR_CODING_REMOTE_REQUEST_INVALID", "the PR request artifact must be a JSON object")
    claimed = request.get("request_sha256")
    body = {key: value for key, value in request.items() if key != "request_sha256"}
    if not isinstance(claimed, str) or _receipt_hash(body) != claimed:
        raise CodingRemoteError("ERROR_CODING_REMOTE_REQUEST_TAMPERED", "request_sha256 does not match the artifact's content")
    shape_ok = (
        body.get("schema_version") == CODING_LIFECYCLE_SCHEMA_VERSION
        and body.get("status") == "CODING_PR_REQUEST_READY"
        and all(isinstance(body.get(key), str) and body[key] for key in ("head_branch", "base_branch", "title", "head_sha"))
        and isinstance(body.get("body"), str)
        and bool(SHA1.fullmatch(body["head_sha"]))
    )
    if not shape_ok:
        raise CodingRemoteError("ERROR_CODING_REMOTE_REQUEST_INVALID", "not a CODING_PR_REQUEST_READY artifact from `mucaro code pr-request`")
    return {**body, "request_sha256": claimed}


def load_request(path: str | Path) -> dict[str, Any]:
    try:
        raw = Path(path).read_bytes()
        if len(raw) > MAX_REQUEST_BYTES:
            raise CodingRemoteError("ERROR_CODING_REMOTE_REQUEST_INVALID", f"request file exceeds {MAX_REQUEST_BYTES} bytes")
        return verify_request(json.loads(raw.decode("utf-8")))
    except (OSError, ValueError) as exc:
        raise CodingRemoteError("ERROR_CODING_REMOTE_REQUEST_INVALID", f"cannot read the PR request artifact: {type(exc).__name__}") from exc


def resolve_github_slug(repo_root: Path) -> str:
    """owner/name from the raw `remote.origin.url` (insteadOf rewrites are deliberately not expanded)."""
    proc = _run(["git", "config", "--get", f"remote.{REMOTE_NAME}.url"], cwd=repo_root, timeout=GIT_TIMEOUT_SECONDS)
    url = proc.stdout.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0 or not url:
        raise CodingRemoteError("ERROR_CODING_REMOTE_NOT_CONFIGURED", f"the repository has no `{REMOTE_NAME}` remote")
    match = GITHUB_REMOTE_URL.fullmatch(url)
    if not match:  # the URL is not echoed: it may carry credentials
        raise CodingRemoteError("ERROR_CODING_REMOTE_HOST_UNSUPPORTED", f"remote.{REMOTE_NAME}.url must be a plain github.com URL without embedded credentials")
    return match.group("slug")


def _prepare(repo_root: str | Path, request: dict[str, Any], cfg: Settings) -> tuple[Path, str]:
    """Prove the repository still yields exactly this request, and name the GitHub repository it targets.

    Rebuilding the request re-checks HEAD, branch, a clean tree and a non-protected head branch; the
    rebuilt request_sha256 must equal the preserved one, so a different repository or content is refused.
    """
    try:
        root = resolve_repository(repo_root, cfg).repo_root
        fresh = build_pull_request_request(
            root,
            expected_head_sha=request["head_sha"],
            head_branch=request["head_branch"],
            base_branch=request["base_branch"],
            title=request["title"],
            body=request["body"],
            cfg=cfg,
        )
    except (CodingWorkspaceError, CodingLifecycleError) as exc:
        raise CodingRemoteError(exc.status, exc.detail) from exc  # drift keeps its precise status
    if fresh["request_sha256"] != request["request_sha256"]:
        raise CodingRemoteError("ERROR_CODING_REMOTE_REQUEST_STALE", "this repository no longer produces the preserved request (different repository root or content)")
    return root, resolve_github_slug(root)


def remote_branch_sha(repo_root: Path, branch: str) -> str | None:
    proc = _run(["git", "ls-remote", REMOTE_NAME, f"refs/heads/{branch}"], cwd=repo_root, timeout=LS_REMOTE_TIMEOUT_SECONDS)
    if proc.returncode != 0:
        raise CodingRemoteError("ERROR_CODING_REMOTE_UNREACHABLE", _tail(proc.stderr) or f"git ls-remote exited {proc.returncode}")
    for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
        sha, _, ref = line.partition("\t")
        if ref == f"refs/heads/{branch}":
            return sha
    return None


def receipt_path(cfg: Settings, kind: str, request: dict[str, Any]) -> Path:
    return cfg.export_dir / "coding_remote" / f"{kind}-{request['request_sha256'][:12]}.json"


def _seal(receipt: dict[str, Any]) -> dict[str, Any]:
    receipt["receipt_sha256"] = _receipt_hash(receipt)
    return receipt


def _save(path: Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def push_branch(repo_root: str | Path, request: dict[str, Any], *, cfg: Settings = settings, dry_run: bool = False) -> dict[str, Any]:
    request = verify_request(request)
    root, slug = _prepare(repo_root, request, cfg)
    branch, head_sha = request["head_branch"], request["head_sha"]
    argv = ["git", "push", "--set-upstream", REMOTE_NAME, f"HEAD:refs/heads/{branch}"]  # never --force
    if dry_run:
        return {"status": "CODING_PUSH_DRY_RUN", "repo_slug": slug, "branch": branch, "head_sha": head_sha, "argv": argv, "request_sha256": request["request_sha256"], "network_action_performed": False}

    proc = _run(argv, cwd=root, timeout=PUSH_TIMEOUT_SECONDS)
    if proc.returncode != 0:
        raise CodingRemoteError("ERROR_CODING_PUSH_FAILED", _tail(proc.stderr) or f"git push exited {proc.returncode}")
    remote_sha = remote_branch_sha(root, branch)
    if remote_sha != head_sha:
        raise CodingRemoteError("ERROR_CODING_PUSH_REMOTE_SHA_MISMATCH", f"expected the remote branch at {head_sha}, found {remote_sha}")
    path = receipt_path(cfg, "push", request)
    receipt = _seal(
        {
            "schema_version": CODING_REMOTE_SCHEMA_VERSION,
            "status": "CODING_PUSH_DONE",
            "repo_slug": slug,
            "remote": REMOTE_NAME,
            "branch": branch,
            "head_sha": head_sha,
            "remote_sha": remote_sha,
            "request_sha256": request["request_sha256"],
            "force": False,
            "network_action_performed": True,
            "credential_handling": CREDENTIAL_HANDLING,
            "invariants": {"request_hash_verified": True, "head_exact": True, "remote_sha_matches_head": True, "head_branch_not_protected": True},
            "receipt_path": str(path),
        }
    )
    _save(path, receipt)
    return receipt


def _created_pull_request_url(stdout: bytes, slug: str) -> tuple[str, int]:
    for line in reversed(stdout.decode("utf-8", errors="replace").splitlines()):
        match = PR_URL.fullmatch(line.strip())
        if match and match.group("slug").lower() == slug.lower():
            return match.group(0), int(match.group("number"))
    raise CodingRemoteError("ERROR_CODING_PR_CREATE_UNPARSEABLE", f"gh reported success but printed no pull request URL for {slug}: {_tail(stdout)}")


def create_pull_request(
    repo_root: str | Path,
    request: dict[str, Any],
    *,
    cfg: Settings = settings,
    draft: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    request = verify_request(request)
    path = receipt_path(cfg, "pr", request)
    if path.is_file() and not dry_run:
        existing = json.loads(path.read_text(encoding="utf-8"))
        raise CodingRemoteError("ERROR_CODING_PR_ALREADY_CREATED", str(existing.get("pr_url")), receipt=existing)
    root, slug = _prepare(repo_root, request, cfg)
    gh = shutil.which("gh")
    if not gh:
        raise CodingRemoteError("ERROR_CODING_PR_GH_UNAVAILABLE", "install the GitHub CLI (gh) and run `gh auth login`; Mucaro never handles the token")
    branch, head_sha = request["head_branch"], request["head_sha"]
    argv = [gh, "pr", "create", "--repo", slug, "--base", request["base_branch"], "--head", branch, "--title", request["title"], "--body-file", "-"]
    if draft:
        argv.append("--draft")
    if dry_run:
        return {"status": "CODING_PR_CREATE_DRY_RUN", "repo_slug": slug, "head_sha": head_sha, "argv": argv, "request_sha256": request["request_sha256"], "network_action_performed": False}

    remote_sha = remote_branch_sha(root, branch)
    if remote_sha is None:
        raise CodingRemoteError("ERROR_CODING_PR_REMOTE_BRANCH_MISSING", f"{branch} is not on {REMOTE_NAME}; run `mucaro code push` first")
    if remote_sha != head_sha:
        raise CodingRemoteError("ERROR_CODING_PR_REMOTE_BRANCH_MISMATCH", f"the remote {branch} is at {remote_sha}, the request is for {head_sha}; push the reviewed commit first")
    proc = _run(argv, cwd=root, timeout=GH_TIMEOUT_SECONDS, stdin=request["body"].encode("utf-8"))
    if proc.returncode != 0:
        raise CodingRemoteError("ERROR_CODING_PR_CREATE_FAILED", _tail(proc.stderr) or _tail(proc.stdout) or f"gh exited {proc.returncode}")
    pr_url, pr_number = _created_pull_request_url(proc.stdout, slug)
    receipt = _seal(
        {
            "schema_version": CODING_REMOTE_SCHEMA_VERSION,
            "status": "CODING_PR_CREATED",
            "repo_slug": slug,
            "pr_url": pr_url,
            "pr_number": pr_number,
            "head_branch": branch,
            "head_sha": head_sha,
            "base_branch": request["base_branch"],
            "draft": draft,
            "request_sha256": request["request_sha256"],
            "network_action_performed": True,
            "credential_handling": CREDENTIAL_HANDLING,
            "invariants": {"request_hash_verified": True, "head_exact": True, "remote_sha_matches_head": True, "head_branch_not_protected": True},
            "receipt_path": str(path),
        }
    )
    _save(path, receipt)
    return receipt
