from __future__ import annotations

import pytest

import backend.app.github_ci as github_ci
from backend.app.github_ci import GitHubCIError, read_github_ci_status


def test_ci_status_binds_latest_run_to_expected_head(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    head = "a" * 40

    def fake_gh(args: list[str], *, timeout_seconds: int = 30):
        calls.append(list(args))
        if args[:2] == ["run", "list"]:
            return [
                {
                    "databaseId": 22,
                    "status": "completed",
                    "conclusion": "success",
                    "headSha": head,
                    "url": "https://example.invalid/run/22",
                    "name": "CI",
                    "event": "push",
                    "createdAt": "2026-10-02T00:00:00Z",
                    "updatedAt": "2026-10-02T00:01:00Z",
                }
            ]
        if args[:2] == ["run", "view"]:
            return {
                "jobs": [
                    {
                        "databaseId": 44,
                        "name": "tests",
                        "status": "completed",
                        "conclusion": "success",
                        "startedAt": "2026-10-02T00:00:05Z",
                        "completedAt": "2026-10-02T00:00:55Z",
                        "url": "https://example.invalid/job/44",
                    }
                ]
            }
        raise AssertionError(args)

    monkeypatch.setattr(github_ci, "_gh_json", fake_gh)
    result = read_github_ci_status(
        repo="jotaele44/mucaro-ai",
        branch="main",
        workflow="CI",
        expected_head_sha=head,
        limit=5,
    )

    assert result["status"] == "GITHUB_CI_STATUS_OK"
    assert result["ci_state"] == "PASS"
    assert result["head_matches"] is True
    assert result["run_count"] == 1
    assert result["latest_jobs"][0]["name"] == "tests"
    assert "--branch" in calls[0]
    assert calls[0][calls[0].index("--limit") + 1] == "5"


def test_ci_status_never_promotes_success_for_another_head(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_gh(args: list[str], *, timeout_seconds: int = 30):
        if args[:2] == ["run", "list"]:
            return [
                {
                    "databaseId": 22,
                    "status": "completed",
                    "conclusion": "success",
                    "headSha": "b" * 40,
                }
            ]
        return {"jobs": []}

    monkeypatch.setattr(github_ci, "_gh_json", fake_gh)
    result = read_github_ci_status(
        repo="jotaele44/mucaro-ai",
        expected_head_sha="a" * 40,
    )

    assert result["ci_state"] == "HEAD_MISMATCH"
    assert result["head_matches"] is False


@pytest.mark.parametrize(
    ("kwargs", "status"),
    [
        ({"repo": "not-a-repo"}, "ERROR_GITHUB_CI_REPO_INVALID"),
        ({"repo": "owner/repo", "expected_head_sha": "ABC"}, "ERROR_GITHUB_CI_HEAD_INVALID"),
        ({"repo": "owner/repo", "limit": 0}, "ERROR_GITHUB_CI_LIMIT"),
        ({"repo": "owner/repo", "limit": 21}, "ERROR_GITHUB_CI_LIMIT"),
        ({"repo": "owner/repo", "branch": "main\n--evil"}, "ERROR_GITHUB_CI_BRANCH_INVALID"),
    ],
)
def test_ci_status_input_negative_controls(kwargs, status: str) -> None:
    with pytest.raises(GitHubCIError) as blocked:
        read_github_ci_status(**kwargs)
    assert blocked.value.status == status
