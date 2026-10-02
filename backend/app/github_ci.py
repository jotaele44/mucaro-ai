from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from typing import Any

from .local_tools import resolve_local_tool

REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
MAX_RUNS = 20
MAX_TOKEN_CHARS = 200


class GitHubCIError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _bounded_token(value: str | None, *, label: str, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise GitHubCIError(f"ERROR_GITHUB_CI_{label.upper()}_REQUIRED", f"{label} is required")
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TOKEN_CHARS:
        raise GitHubCIError(f"ERROR_GITHUB_CI_{label.upper()}_INVALID", f"{label} must be a non-empty bounded string")
    cleaned = value.strip()
    if any(char in cleaned for char in ("\x00", "\n", "\r")):
        raise GitHubCIError(f"ERROR_GITHUB_CI_{label.upper()}_INVALID", f"{label} contains a control character")
    return cleaned


def _gh_json(args: list[str], *, timeout_seconds: int = 30) -> Any:
    gh = resolve_local_tool("gh")
    if not gh:
        raise GitHubCIError("ERROR_GITHUB_CI_GH_UNAVAILABLE", "GitHub CLI is not available in this runtime")
    try:
        proc = subprocess.run(
            [gh, *args],
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitHubCIError("ERROR_GITHUB_CI_TIMEOUT", f"gh command exceeded {timeout_seconds}s") from exc
    except OSError as exc:
        raise GitHubCIError("ERROR_GITHUB_CI_GH_UNAVAILABLE", "GitHub CLI could not be started") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "gh command failed").strip()
        raise GitHubCIError("ERROR_GITHUB_CI_REMOTE", detail)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise GitHubCIError("ERROR_GITHUB_CI_JSON", f"GitHub CLI returned invalid JSON: {exc}") from exc


def _normalize_run(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "database_id": row.get("databaseId"),
        "status": row.get("status"),
        "conclusion": row.get("conclusion"),
        "head_sha": row.get("headSha"),
        "url": row.get("url"),
        "name": row.get("name"),
        "event": row.get("event"),
        "created_at": row.get("createdAt"),
        "updated_at": row.get("updatedAt"),
    }


def _ci_state(latest: dict[str, Any] | None, expected_head_sha: str | None) -> str:
    if latest is None:
        return "NO_RUNS"
    if expected_head_sha and latest.get("head_sha") != expected_head_sha:
        return "HEAD_MISMATCH"
    status = latest.get("status")
    conclusion = latest.get("conclusion")
    if status == "completed":
        return "PASS" if conclusion == "success" else "FAIL"
    return "PENDING"


def read_github_ci_status(
    *,
    repo: str,
    branch: str | None = None,
    workflow: str = "CI",
    expected_head_sha: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    repo = _bounded_token(repo, label="repo", required=True) or ""
    if not REPO_PATTERN.fullmatch(repo):
        raise GitHubCIError("ERROR_GITHUB_CI_REPO_INVALID", "repo must be owner/name")
    branch = _bounded_token(branch, label="branch") if branch is not None else None
    workflow = _bounded_token(workflow, label="workflow", required=True) or "CI"
    if expected_head_sha is not None:
        expected_head_sha = _bounded_token(expected_head_sha, label="head")
        if expected_head_sha is None or not SHA_PATTERN.fullmatch(expected_head_sha):
            raise GitHubCIError("ERROR_GITHUB_CI_HEAD_INVALID", "expected_head_sha must be a lowercase 40-character SHA")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > MAX_RUNS:
        raise GitHubCIError("ERROR_GITHUB_CI_LIMIT", f"limit must be 1..{MAX_RUNS}")

    args = [
        "run",
        "list",
        "--repo",
        repo,
        "--workflow",
        workflow,
        "--limit",
        str(limit),
        "--json",
        "databaseId,status,conclusion,headSha,url,name,event,createdAt,updatedAt",
    ]
    if branch:
        args.extend(["--branch", branch])
    payload = _gh_json(args)
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise GitHubCIError("ERROR_GITHUB_CI_SHAPE", "GitHub run list did not return an array of run objects")

    runs = [_normalize_run(row) for row in payload]
    latest = runs[0] if runs else None
    state = _ci_state(latest, expected_head_sha)

    jobs: list[dict[str, Any]] | None = None
    jobs_error: str | None = None
    if latest and isinstance(latest.get("database_id"), int):
        try:
            view = _gh_json(["run", "view", str(latest["database_id"]), "--repo", repo, "--json", "jobs"])
            raw_jobs = view.get("jobs") if isinstance(view, dict) else None
            if isinstance(raw_jobs, list):
                jobs = [
                    {
                        "database_id": job.get("databaseId"),
                        "name": job.get("name"),
                        "status": job.get("status"),
                        "conclusion": job.get("conclusion"),
                        "started_at": job.get("startedAt"),
                        "completed_at": job.get("completedAt"),
                        "url": job.get("url"),
                    }
                    for job in raw_jobs
                    if isinstance(job, dict)
                ]
        except GitHubCIError as exc:
            jobs_error = f"{exc.status}:{exc.detail}"

    return {
        "status": "GITHUB_CI_STATUS_OK",
        "retrieved_utc": _utc_now(),
        "repo": repo,
        "branch": branch,
        "workflow": workflow,
        "expected_head_sha": expected_head_sha,
        "run_count": len(runs),
        "runs": runs,
        "latest": latest,
        "latest_jobs": jobs,
        "latest_jobs_error": jobs_error,
        "ci_state": state,
        "head_matches": None if not expected_head_sha or latest is None else latest.get("head_sha") == expected_head_sha,
        "limitations": [
            "This is a read-only GitHub Actions status adapter; it does not trigger, rerun, cancel, or mutate workflows.",
            "GitHub is an inherently online dependency and requires an authenticated GitHub CLI session.",
            "NO_RUNS and HEAD_MISMATCH are evidence states, not proof that CI is absent or broken.",
        ],
        "falsification_criteria": [
            "reject malformed repository, workflow, branch, head, or result-limit inputs",
            "fail closed when the GitHub CLI is unavailable, remote access fails, or JSON shape is invalid",
            "never classify a successful run for a different head SHA as current-head PASS",
        ],
    }
