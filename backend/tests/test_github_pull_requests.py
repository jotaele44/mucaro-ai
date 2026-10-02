from __future__ import annotations

from pathlib import Path

import pytest

import backend.app.github_pull_requests as github_pr
from backend.app.config import Settings
from backend.app.github_pull_requests import GitHubPullRequestError, open_or_update_pull_request


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


def approved_request(head: str, request_hash: str, *, title: str = "Title", body: str = "Body") -> dict:
    return {
        "status": "CODING_PR_REQUEST_READY",
        "head_sha": head,
        "head_branch": "feature/video-cli",
        "base_branch": "main",
        "title": title,
        "body": body,
        "request_sha256": request_hash,
    }


def test_pr_create_requires_exact_request_and_remote_head_and_keeps_body_out_of_argv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = make_settings(tmp_path)
    head = "a" * 40
    request_hash = "b" * 64
    calls: list[tuple[list[str], str | None]] = []
    list_calls = {"n": 0}

    monkeypatch.setattr(
        github_pr,
        "build_pull_request_request",
        lambda *args, **kwargs: approved_request(head, request_hash),
    )
    monkeypatch.setattr(github_pr, "_local_origin_repo", lambda repo_root: "jotaele44/mucaro-ai")
    monkeypatch.setattr(github_pr, "_remote_branch_sha", lambda repo, branch: head)

    def fake_list(repo, *, head_branch, base_branch):
        list_calls["n"] += 1
        if list_calls["n"] == 1:
            return []
        return [
            {
                "number": 187,
                "url": "https://github.com/jotaele44/mucaro-ai/pull/187",
                "title": "Title",
                "body": "Body",
                "headRefName": "feature/video-cli",
                "headRefOid": head,
                "baseRefName": "main",
                "isDraft": False,
            }
        ]

    def fake_gh(args, *, timeout_seconds=45, input_text=None):
        calls.append((list(args), input_text))
        return "https://github.com/jotaele44/mucaro-ai/pull/187\n"

    monkeypatch.setattr(github_pr, "_open_prs", fake_list)
    monkeypatch.setattr(github_pr, "_gh", fake_gh)

    result = open_or_update_pull_request(
        str(tmp_path / "repo"),
        remote_repo="jotaele44/mucaro-ai",
        expected_head_sha=head,
        head_branch="feature/video-cli",
        base_branch="main",
        title="Title",
        body="Body",
        expected_request_sha256=request_hash,
        cfg=cfg,
    )

    assert result["action"] == "CREATED"
    assert result["network_action_performed"] is True
    assert result["invariants"] == {
        "local_request_exact": True,
        "origin_repo_exact": True,
        "remote_head_exact": True,
        "pr_head_exact": True,
        "pr_head_branch_exact": True,
        "pr_base_branch_exact": True,
    }
    assert len(calls) == 1
    argv, stdin_body = calls[0]
    assert "--body-file" in argv
    assert "Body" not in argv
    assert stdin_body == "Body"


def test_pr_update_revalidates_existing_identity_and_postcondition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = make_settings(tmp_path)
    head = "c" * 40
    request_hash = "d" * 64
    mutation_calls: list[tuple[list[str], str | None]] = []
    existing = {
        "number": 99,
        "url": "https://example.invalid/pr/99",
        "title": "Old",
        "body": "Old body",
        "headRefName": "feature/video-cli",
        "headRefOid": head,
        "baseRefName": "main",
        "isDraft": False,
    }
    updated = {
        **existing,
        "title": "New",
        "body": "New body",
        "state": "OPEN",
    }

    monkeypatch.setattr(
        github_pr,
        "build_pull_request_request",
        lambda *args, **kwargs: approved_request(head, request_hash, title="New", body="New body"),
    )
    monkeypatch.setattr(github_pr, "_local_origin_repo", lambda repo_root: "jotaele44/mucaro-ai")
    monkeypatch.setattr(github_pr, "_remote_branch_sha", lambda repo, branch: head)
    monkeypatch.setattr(github_pr, "_open_prs", lambda *args, **kwargs: [existing])
    monkeypatch.setattr(github_pr, "_view_pr", lambda repo, number: updated)

    def fake_gh(args, *, timeout_seconds=45, input_text=None):
        mutation_calls.append((list(args), input_text))
        return ""

    monkeypatch.setattr(github_pr, "_gh", fake_gh)

    result = open_or_update_pull_request(
        str(tmp_path / "repo"),
        remote_repo="jotaele44/mucaro-ai",
        expected_head_sha=head,
        head_branch="feature/video-cli",
        base_branch="main",
        title="New",
        body="New body",
        expected_request_sha256=request_hash,
        cfg=cfg,
    )

    assert result["action"] == "UPDATED"
    assert result["pull_request"]["number"] == 99
    assert mutation_calls[0][1] == "New body"
    assert "New body" not in mutation_calls[0][0]


def test_pr_remote_head_drift_blocks_before_remote_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = make_settings(tmp_path)
    head = "e" * 40
    request_hash = "f" * 64
    monkeypatch.setattr(
        github_pr,
        "build_pull_request_request",
        lambda *args, **kwargs: approved_request(head, request_hash),
    )
    monkeypatch.setattr(github_pr, "_local_origin_repo", lambda repo_root: "jotaele44/mucaro-ai")
    monkeypatch.setattr(github_pr, "_remote_branch_sha", lambda repo, branch: "1" * 40)
    monkeypatch.setattr(
        github_pr,
        "_open_prs",
        lambda *args, **kwargs: pytest.fail("PR listing must not occur after remote head drift"),
    )

    with pytest.raises(GitHubPullRequestError) as blocked:
        open_or_update_pull_request(
            str(tmp_path / "repo"),
            remote_repo="jotaele44/mucaro-ai",
            expected_head_sha=head,
            head_branch="feature/video-cli",
            base_branch="main",
            title="Title",
            body="Body",
            expected_request_sha256=request_hash,
            cfg=cfg,
        )
    assert blocked.value.status == "ERROR_GITHUB_PR_REMOTE_HEAD_DRIFT"


def test_pr_request_hash_drift_blocks_before_remote_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = make_settings(tmp_path)
    head = "2" * 40
    monkeypatch.setattr(
        github_pr,
        "build_pull_request_request",
        lambda *args, **kwargs: approved_request(head, "3" * 64),
    )
    monkeypatch.setattr(
        github_pr,
        "_remote_branch_sha",
        lambda *args, **kwargs: pytest.fail("remote lookup must not occur after request drift"),
    )

    with pytest.raises(GitHubPullRequestError) as blocked:
        open_or_update_pull_request(
            str(tmp_path / "repo"),
            remote_repo="jotaele44/mucaro-ai",
            expected_head_sha=head,
            head_branch="feature/video-cli",
            base_branch="main",
            title="Title",
            body="Body",
            expected_request_sha256="4" * 64,
            cfg=cfg,
        )
    assert blocked.value.status == "ERROR_GITHUB_PR_REQUEST_DRIFT"


@pytest.mark.parametrize(
    ("overrides", "status"),
    [
        ({"remote_repo": "bad"}, "ERROR_GITHUB_PR_REPO_INVALID"),
        ({"expected_head_sha": "ABC"}, "ERROR_GITHUB_PR_HEAD_INVALID"),
        ({"head_branch": "feature/x\n--evil"}, "ERROR_GITHUB_PR_HEAD_BRANCH_INVALID"),
        ({"expected_request_sha256": "x"}, "ERROR_GITHUB_PR_REQUEST_HASH_INVALID"),
    ],
)
def test_pr_input_negative_controls(tmp_path: Path, overrides: dict, status: str) -> None:
    cfg = make_settings(tmp_path)
    args = {
        "repo_root": str(tmp_path / "repo"),
        "remote_repo": "jotaele44/mucaro-ai",
        "expected_head_sha": "a" * 40,
        "head_branch": "feature/video-cli",
        "base_branch": "main",
        "title": "Title",
        "body": "Body",
        "expected_request_sha256": "b" * 64,
        "cfg": cfg,
    }
    args.update(overrides)
    with pytest.raises(GitHubPullRequestError) as blocked:
        open_or_update_pull_request(**args)
    assert blocked.value.status == status

def test_pr_origin_mismatch_blocks_before_remote_head_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = make_settings(tmp_path)
    head = "7" * 40
    request_hash = "8" * 64
    monkeypatch.setattr(
        github_pr,
        "build_pull_request_request",
        lambda *args, **kwargs: {
            **approved_request(head, request_hash),
            "repo_root": str(tmp_path / "repo"),
        },
    )
    monkeypatch.setattr(github_pr, "_local_origin_repo", lambda repo_root: "someone/fork")
    monkeypatch.setattr(
        github_pr,
        "_remote_branch_sha",
        lambda *args, **kwargs: pytest.fail("remote lookup must not occur after origin mismatch"),
    )

    with pytest.raises(GitHubPullRequestError) as blocked:
        open_or_update_pull_request(
            str(tmp_path / "repo"),
            remote_repo="jotaele44/mucaro-ai",
            expected_head_sha=head,
            head_branch="feature/video-cli",
            base_branch="main",
            title="Title",
            body="Body",
            expected_request_sha256=request_hash,
            cfg=cfg,
        )
    assert blocked.value.status == "ERROR_GITHUB_PR_ORIGIN_MISMATCH"

