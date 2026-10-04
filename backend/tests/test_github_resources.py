from __future__ import annotations

from pathlib import Path

import pytest

import backend.app.github_resources as resources
import backend.app.tools as tools_module
from backend.app.config import Settings
from backend.app.github_resources import GitHubResourceError
from backend.app.tools import approve_tool_call, list_tool_calls, request_tool_call, run_tool_call


def make_settings(root: Path) -> Settings:
    cfg = Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "test.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )
    cfg.ensure_dirs()
    return cfg


def test_issue_search_preserves_repository_identity_and_search_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_api(path, *, params=None, timeout_seconds=45):
        captured.update({"path": path, "params": params, "timeout": timeout_seconds})
        return {
            "total_count": 1500,
            "incomplete_results": False,
            "items": [
                {
                    "number": 7,
                    "title": "Issue seven",
                    "body": "body",
                    "state": "open",
                    "html_url": "https://github.com/owner/repo/issues/7",
                    "user": {"login": "alice"},
                    "labels": [{"name": "bug"}],
                    "assignees": [{"login": "bob"}],
                    "comments": 3,
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-02T00:00:00Z",
                    "closed_at": None,
                }
            ],
        }

    monkeypatch.setattr(resources, "_api_json", fake_api)
    result = resources.list_github_issues(repo="owner/repo", state="open", page=1, per_page=30)

    assert result["status"] == "GITHUB_ISSUES_OK"
    assert result["repository"] == "owner/repo"
    assert result["issues"][0]["number"] == 7
    assert result["issues"][0]["repository"] == "owner/repo"
    assert result["total_count"] == 1500
    assert result["search_result_ceiling"] == 1000
    assert result["has_more"] is True
    assert captured["path"] == "search/issues"
    assert "is:issue" in captured["params"]["q"]


def test_issue_view_rejects_pull_request_manifestation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        resources,
        "_api_json",
        lambda path, **kwargs: {
            "number": 9,
            "title": "Actually a PR",
            "state": "open",
            "pull_request": {"url": "https://api.github.com/repos/owner/repo/pulls/9"},
        },
    )

    with pytest.raises(GitHubResourceError) as exc:
        resources.get_github_issue(repo="owner/repo", number=9)
    assert exc.value.status == "ERROR_GITHUB_ISSUE_IS_PR"


def test_pull_request_provider_preserves_head_sha_and_bounds_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        resources,
        "_api_json",
        lambda path, **kwargs: [
            {
                "number": 12,
                "title": "PR twelve",
                "body": "x" * (resources.MAX_BODY_CHARS + 5),
                "state": "open",
                "draft": False,
                "html_url": "https://github.com/owner/repo/pull/12",
                "user": {"login": "alice"},
                "head": {"ref": "feat/x", "sha": "a" * 40},
                "base": {"ref": "main"},
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-02T00:00:00Z",
                "closed_at": None,
                "merged_at": None,
            }
        ],
    )
    result = resources.list_github_pull_requests(repo="owner/repo", per_page=1)

    pr = result["pull_requests"][0]
    assert pr["repository"] == "owner/repo"
    assert pr["head_ref"] == "feat/x"
    assert pr["head_sha"] == "a" * 40
    assert pr["base_ref"] == "main"
    assert len(pr["body"]) == resources.MAX_BODY_CHARS
    assert pr["body_truncated"] is True
    assert result["has_more"] is True


def test_gist_view_is_identity_checked_and_content_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    files = {
        f"file-{index:02d}.txt": {
            "filename": f"file-{index:02d}.txt",
            "type": "text/plain",
            "language": "Text",
            "size": 25000,
            "truncated": False,
            "content": "x" * (resources.MAX_GIST_CONTENT_CHARS + 10),
        }
        for index in range(resources.MAX_GIST_FILES + 5)
    }
    monkeypatch.setattr(
        resources,
        "_api_json",
        lambda path, **kwargs: {
            "id": "abcdef1234567890",
            "description": "bounded",
            "public": False,
            "html_url": "https://gist.github.com/abcdef1234567890",
            "owner": {"login": "alice"},
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
            "files": files,
        },
    )
    result = resources.get_github_gist(gist_id="abcdef1234567890")

    gist = result["gist"]
    assert gist["id"] == "abcdef1234567890"
    assert gist["file_count"] == resources.MAX_GIST_FILES + 5
    assert len(gist["files"]) == resources.MAX_GIST_FILES
    assert gist["files_truncated_by_provider"] is True
    assert len(gist["files"][0]["content"]) == resources.MAX_GIST_CONTENT_CHARS
    assert gist["files"][0]["content_truncated_by_provider"] is True


def test_gist_view_rejects_identity_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        resources,
        "_api_json",
        lambda path, **kwargs: {
            "id": "1111111111111111",
            "files": {},
        },
    )
    with pytest.raises(GitHubResourceError) as exc:
        resources.get_github_gist(gist_id="abcdef1234567890")
    assert exc.value.status == "ERROR_GITHUB_GIST_IDENTITY"


def test_all_github_resource_tools_flow_through_governed_approval(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = make_settings(tmp_path)
    monkeypatch.setattr(tools_module, "list_github_issues", lambda **kwargs: {"status": "GITHUB_ISSUES_OK"})
    monkeypatch.setattr(tools_module, "get_github_issue", lambda **kwargs: {"status": "GITHUB_ISSUE_OK"})
    monkeypatch.setattr(tools_module, "list_github_pull_requests", lambda **kwargs: {"status": "GITHUB_PRS_OK"})
    monkeypatch.setattr(tools_module, "get_github_pull_request", lambda **kwargs: {"status": "GITHUB_PR_OK"})
    monkeypatch.setattr(tools_module, "list_github_gists", lambda **kwargs: {"status": "GITHUB_GISTS_OK"})
    monkeypatch.setattr(tools_module, "get_github_gist", lambda **kwargs: {"status": "GITHUB_GIST_OK"})

    for name, arguments in (
        ("github.issues_list", {"repo": "owner/repo"}),
        ("github.issue_view", {"repo": "owner/repo", "number": 1}),
        ("github.prs_list", {"repo": "owner/repo"}),
        ("github.pr_view", {"repo": "owner/repo", "number": 2}),
        ("github.gists_list", {"owner": "owner"}),
        ("github.gist_view", {"gist_id": "abcdef1234567890"}),
    ):
        call = request_tool_call(name, arguments, cfg=cfg, requested_by="test")["tool_call"]
        approve_tool_call(
            call["tool_call_id"],
            cfg=cfg,
            approved_by="test",
            expected_arguments_sha256=call["arguments_sha256"],
        )
        executed = run_tool_call(call["tool_call_id"], cfg=cfg, executed_by="test")
        assert executed["tool_call"]["status"] == "EXECUTED"

    by_name = {call["tool_name"]: call for call in list_tool_calls(cfg)["tool_calls"]}
    assert set(by_name) == {
        "github.issues_list",
        "github.issue_view",
        "github.prs_list",
        "github.pr_view",
        "github.gists_list",
        "github.gist_view",
    }
    assert all(call["status"] == "EXECUTED" for call in by_name.values())


def test_issue_search_refuses_pages_outside_github_retrievable_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = {"value": False}

    def fake_api(*args, **kwargs):
        called["value"] = True
        return {}

    monkeypatch.setattr(resources, "_api_json", fake_api)
    with pytest.raises(GitHubResourceError) as exc:
        resources.list_github_issues(repo="owner/repo", page=11, per_page=100)
    assert exc.value.status == "ERROR_GITHUB_ISSUE_SEARCH_WINDOW"
    assert called["value"] is False


def test_all_remote_resource_results_expose_retrieval_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    timestamp = "2026-10-03T20:00:00Z"
    monkeypatch.setattr(resources, "utc_now", lambda: timestamp)

    issue = {
        "number": 1,
        "title": "Issue",
        "body": None,
        "state": "open",
        "html_url": "https://github.com/owner/repo/issues/1",
        "user": {"login": "alice"},
        "labels": [],
        "assignees": [],
        "comments": 0,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "closed_at": None,
    }
    pr = {
        "number": 2,
        "title": "PR",
        "body": None,
        "state": "open",
        "draft": False,
        "html_url": "https://github.com/owner/repo/pull/2",
        "user": {"login": "alice"},
        "head": {"ref": "feat/x", "sha": "a" * 40},
        "base": {"ref": "main"},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "closed_at": None,
        "merged_at": None,
    }
    gist = {
        "id": "abcdef1234567890",
        "description": "g",
        "public": True,
        "html_url": "https://gist.github.com/abcdef1234567890",
        "owner": {"login": "alice"},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "files": {},
    }

    def fake_api(path, *, params=None, timeout_seconds=45):
        if path == "search/issues":
            return {"total_count": 1, "incomplete_results": False, "items": [issue]}
        if path == "repos/owner/repo/issues/1":
            return issue
        if path == "repos/owner/repo/pulls":
            return [pr]
        if path == "repos/owner/repo/pulls/2":
            return pr
        if path == "users/alice/gists":
            return [gist]
        if path == "gists/abcdef1234567890":
            return gist
        raise AssertionError(path)

    monkeypatch.setattr(resources, "_api_json", fake_api)

    results = [
        resources.list_github_issues(repo="owner/repo"),
        resources.get_github_issue(repo="owner/repo", number=1),
        resources.list_github_pull_requests(repo="owner/repo"),
        resources.get_github_pull_request(repo="owner/repo", number=2),
        resources.list_github_gists(owner="alice"),
        resources.get_github_gist(gist_id="abcdef1234567890"),
    ]
    assert all(result["retrieved_utc"] == timestamp for result in results)
