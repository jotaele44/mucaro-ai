from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from .local_tools import resolve_local_tool

REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
OWNER_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,100}$")
GIST_ID_PATTERN = re.compile(r"^[0-9A-Fa-f]{8,64}$")
ISSUE_STATES = {"open", "closed", "all"}
PR_STATES = {"open", "closed", "all"}
MAX_PAGE = 10_000
MAX_PER_PAGE = 100
MAX_ERROR_CHARS = 4000
MAX_BODY_CHARS = 20_000
MAX_GIST_FILES = 50
MAX_GIST_CONTENT_CHARS = 20_000
SEARCH_RESULT_CEILING = 1000


class GitHubResourceError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _repo(value: str) -> str:
    if not isinstance(value, str) or not REPO_PATTERN.fullmatch(value.strip()):
        raise GitHubResourceError("ERROR_GITHUB_RESOURCE_REPO", "repo must be owner/name")
    return value.strip()


def _owner(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not OWNER_PATTERN.fullmatch(value.strip()):
        raise GitHubResourceError("ERROR_GITHUB_RESOURCE_OWNER", "owner is invalid")
    return value.strip()


def _page_args(*, page: int, per_page: int) -> tuple[int, int]:
    if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= MAX_PAGE:
        raise GitHubResourceError("ERROR_GITHUB_RESOURCE_PAGE", f"page must be 1..{MAX_PAGE}")
    if not isinstance(per_page, int) or isinstance(per_page, bool) or not 1 <= per_page <= MAX_PER_PAGE:
        raise GitHubResourceError("ERROR_GITHUB_RESOURCE_PER_PAGE", f"per_page must be 1..{MAX_PER_PAGE}")
    return page, per_page


def _api_json(path: str, *, params: dict[str, str | int] | None = None, timeout_seconds: int = 45) -> Any:
    gh = resolve_local_tool("gh")
    if not gh:
        raise GitHubResourceError("ERROR_GITHUB_RESOURCE_GH_UNAVAILABLE", "GitHub CLI is not available in this runtime")
    argv = [gh, "api", "--method", "GET", path]
    for key, value in (params or {}).items():
        argv.extend(["-f", f"{key}={value}"])
    try:
        proc = subprocess.run(
            argv,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitHubResourceError("ERROR_GITHUB_RESOURCE_TIMEOUT", f"GitHub API call exceeded {timeout_seconds}s") from exc
    except OSError as exc:
        raise GitHubResourceError("ERROR_GITHUB_RESOURCE_GH_UNAVAILABLE", "GitHub CLI could not be started") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "GitHub API request failed").strip()
        raise GitHubResourceError("ERROR_GITHUB_RESOURCE_REMOTE", detail[:MAX_ERROR_CHARS])
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise GitHubResourceError("ERROR_GITHUB_RESOURCE_JSON", f"GitHub API returned invalid JSON: {exc}") from exc


def _user_login(value: Any) -> str | None:
    return value.get("login") if isinstance(value, dict) and isinstance(value.get("login"), str) else None


def _labels(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    labels: list[str] = []
    for item in value:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            labels.append(item["name"])
    return labels


def _assignees(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [login for item in value if (login := _user_login(item)) is not None]


def _bounded_text(value: Any, *, max_chars: int) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    if not isinstance(value, str):
        raise GitHubResourceError("ERROR_GITHUB_RESOURCE_TEXT_SHAPE", "expected string or null")
    return value[:max_chars], len(value) > max_chars


def _normalize_issue(repo: str, row: dict[str, Any]) -> dict[str, Any]:
    number = row.get("number")
    if not isinstance(number, int) or isinstance(number, bool):
        raise GitHubResourceError("ERROR_GITHUB_ISSUE_SHAPE", "issue row has no numeric number")
    if "pull_request" in row:
        raise GitHubResourceError("ERROR_GITHUB_ISSUE_IS_PR", f"{repo}#{number}")
    body, body_truncated = _bounded_text(row.get("body"), max_chars=MAX_BODY_CHARS)
    return {
        "repository": repo,
        "number": number,
        "title": row.get("title"),
        "body": body,
        "body_truncated": body_truncated,
        "state": row.get("state"),
        "url": row.get("html_url"),
        "author": _user_login(row.get("user")),
        "labels": _labels(row.get("labels")),
        "assignees": _assignees(row.get("assignees")),
        "comments": row.get("comments"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "closed_at": row.get("closed_at"),
    }


def list_github_issues(
    *,
    repo: str,
    state: str = "open",
    page: int = 1,
    per_page: int = 30,
) -> dict[str, Any]:
    repo = _repo(repo)
    if state not in ISSUE_STATES:
        raise GitHubResourceError("ERROR_GITHUB_ISSUE_STATE", state)
    page, per_page = _page_args(page=page, per_page=per_page)
    if (page - 1) * per_page >= SEARCH_RESULT_CEILING:
        raise GitHubResourceError(
            "ERROR_GITHUB_ISSUE_SEARCH_WINDOW",
            f"GitHub Search exposes only the first {SEARCH_RESULT_CEILING} results",
        )
    qualifiers = [f"repo:{repo}", "is:issue"]
    if state != "all":
        qualifiers.append(f"state:{state}")
    payload = _api_json(
        "search/issues",
        params={"q": " ".join(qualifiers), "page": page, "per_page": per_page},
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise GitHubResourceError("ERROR_GITHUB_ISSUE_LIST_SHAPE", "issue search did not return an items array")
    items = []
    for row in payload["items"]:
        if not isinstance(row, dict):
            raise GitHubResourceError("ERROR_GITHUB_ISSUE_LIST_SHAPE", "issue search item is not an object")
        items.append(_normalize_issue(repo, row))
    total = payload.get("total_count")
    if not isinstance(total, int) or isinstance(total, bool):
        raise GitHubResourceError("ERROR_GITHUB_ISSUE_LIST_SHAPE", "issue search has no numeric total_count")
    return {
        "status": "GITHUB_ISSUES_OK",
        "repository": repo,
        "state": state,
        "page": page,
        "per_page": per_page,
        "returned_count": len(items),
        "total_count": total,
        "incomplete_results": payload.get("incomplete_results") is True,
        "search_result_ceiling": SEARCH_RESULT_CEILING,
        "has_more": page * per_page < min(total, SEARCH_RESULT_CEILING),
        "issues": items,
        "limitations": [
            "GitHub Search exposes at most the first 1000 matching results; total_count may exceed the retrievable search window",
            "search pagination is discovery evidence unless pages are followed through the retrievable ceiling",
        ],
    }


def get_github_issue(*, repo: str, number: int) -> dict[str, Any]:
    repo = _repo(repo)
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        raise GitHubResourceError("ERROR_GITHUB_ISSUE_NUMBER", "issue number must be positive")
    payload = _api_json(f"repos/{repo}/issues/{number}")
    if not isinstance(payload, dict):
        raise GitHubResourceError("ERROR_GITHUB_ISSUE_SHAPE", "issue view did not return an object")
    issue = _normalize_issue(repo, payload)
    if issue["number"] != number:
        raise GitHubResourceError("ERROR_GITHUB_ISSUE_IDENTITY", f"expected {number}, observed {issue['number']}")
    return {"status": "GITHUB_ISSUE_OK", "issue": issue}


def _normalize_pr(repo: str, row: dict[str, Any]) -> dict[str, Any]:
    number = row.get("number")
    if not isinstance(number, int) or isinstance(number, bool):
        raise GitHubResourceError("ERROR_GITHUB_PR_RESOURCE_SHAPE", "pull request row has no numeric number")
    head = row.get("head") if isinstance(row.get("head"), dict) else {}
    base = row.get("base") if isinstance(row.get("base"), dict) else {}
    body, body_truncated = _bounded_text(row.get("body"), max_chars=MAX_BODY_CHARS)
    return {
        "repository": repo,
        "number": number,
        "title": row.get("title"),
        "body": body,
        "body_truncated": body_truncated,
        "state": row.get("state"),
        "draft": row.get("draft") is True,
        "url": row.get("html_url"),
        "author": _user_login(row.get("user")),
        "head_ref": head.get("ref"),
        "head_sha": head.get("sha"),
        "base_ref": base.get("ref"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "closed_at": row.get("closed_at"),
        "merged_at": row.get("merged_at"),
    }


def list_github_pull_requests(
    *,
    repo: str,
    state: str = "open",
    page: int = 1,
    per_page: int = 30,
) -> dict[str, Any]:
    repo = _repo(repo)
    if state not in PR_STATES:
        raise GitHubResourceError("ERROR_GITHUB_PR_RESOURCE_STATE", state)
    page, per_page = _page_args(page=page, per_page=per_page)
    payload = _api_json(
        f"repos/{repo}/pulls",
        params={"state": state, "page": page, "per_page": per_page},
    )
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise GitHubResourceError("ERROR_GITHUB_PR_RESOURCE_LIST_SHAPE", "pull request list did not return an array")
    items = [_normalize_pr(repo, row) for row in payload]
    return {
        "status": "GITHUB_PRS_OK",
        "repository": repo,
        "state": state,
        "page": page,
        "per_page": per_page,
        "returned_count": len(items),
        "has_more": len(items) == per_page,
        "pull_requests": items,
        "limitations": [
            "has_more is a page-continuation hint, not proof that the next page contains a row",
            "list pagination is bounded and does not claim repository-wide exhaustion unless pages are followed to an empty/short page",
        ],
    }


def get_github_pull_request(*, repo: str, number: int) -> dict[str, Any]:
    repo = _repo(repo)
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        raise GitHubResourceError("ERROR_GITHUB_PR_RESOURCE_NUMBER", "pull request number must be positive")
    payload = _api_json(f"repos/{repo}/pulls/{number}")
    if not isinstance(payload, dict):
        raise GitHubResourceError("ERROR_GITHUB_PR_RESOURCE_SHAPE", "pull request view did not return an object")
    pr = _normalize_pr(repo, payload)
    if pr["number"] != number:
        raise GitHubResourceError("ERROR_GITHUB_PR_RESOURCE_IDENTITY", f"expected {number}, observed {pr['number']}")
    return {"status": "GITHUB_PR_OK", "pull_request": pr}


def _normalize_gist(row: dict[str, Any], *, include_content: bool = False) -> dict[str, Any]:
    gist_id = row.get("id")
    if not isinstance(gist_id, str) or not GIST_ID_PATTERN.fullmatch(gist_id):
        raise GitHubResourceError("ERROR_GITHUB_GIST_SHAPE", "gist has no canonical hexadecimal id")
    raw_files = row.get("files")
    files: list[dict[str, Any]] = []
    raw_names = sorted(raw_files) if isinstance(raw_files, dict) else []
    for name in raw_names[:MAX_GIST_FILES]:
        item = raw_files[name]
        if not isinstance(item, dict):
            continue
        normalized = {
            "filename": item.get("filename") or name,
            "type": item.get("type"),
            "language": item.get("language"),
            "size": item.get("size"),
            "truncated": item.get("truncated"),
        }
        if include_content:
            content, content_truncated = _bounded_text(item.get("content"), max_chars=MAX_GIST_CONTENT_CHARS)
            normalized["content"] = content
            normalized["content_truncated_by_provider"] = content_truncated
        files.append(normalized)
    return {
        "id": gist_id,
        "description": row.get("description"),
        "public": row.get("public") is True,
        "url": row.get("html_url"),
        "owner": _user_login(row.get("owner")),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "file_count": len(raw_names),
        "files_truncated_by_provider": len(raw_names) > MAX_GIST_FILES,
        "files": files,
    }

def list_github_gists(
    *,
    owner: str | None = None,
    page: int = 1,
    per_page: int = 30,
) -> dict[str, Any]:
    owner = _owner(owner)
    page, per_page = _page_args(page=page, per_page=per_page)
    path = f"users/{owner}/gists" if owner else "gists"
    payload = _api_json(path, params={"page": page, "per_page": per_page})
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise GitHubResourceError("ERROR_GITHUB_GIST_LIST_SHAPE", "gist list did not return an array")
    items = [_normalize_gist(row) for row in payload]
    return {
        "status": "GITHUB_GISTS_OK",
        "owner": owner,
        "scope": "PUBLIC_OWNER" if owner else "AUTHENTICATED_USER",
        "page": page,
        "per_page": per_page,
        "returned_count": len(items),
        "has_more": len(items) == per_page,
        "gists": items,
        "limitations": [
            "has_more is a page-continuation hint, not proof that the next page contains a row",
            "omitting owner uses the authenticated GitHub account and may include private gists visible to that account",
        ],
    }


def get_github_gist(*, gist_id: str) -> dict[str, Any]:
    if not isinstance(gist_id, str) or not GIST_ID_PATTERN.fullmatch(gist_id):
        raise GitHubResourceError("ERROR_GITHUB_GIST_ID", "gist_id must be a hexadecimal GitHub gist id")
    payload = _api_json(f"gists/{gist_id}")
    if not isinstance(payload, dict):
        raise GitHubResourceError("ERROR_GITHUB_GIST_SHAPE", "gist view did not return an object")
    gist = _normalize_gist(payload, include_content=True)
    if gist["id"].lower() != gist_id.lower():
        raise GitHubResourceError("ERROR_GITHUB_GIST_IDENTITY", f"expected {gist_id}, observed {gist['id']}")
    return {"status": "GITHUB_GIST_OK", "gist": gist}
