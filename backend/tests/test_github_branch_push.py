from __future__ import annotations

from types import SimpleNamespace

import pytest

import backend.app.github_branch_push as push
from backend.app.github_branch_push import GitHubBranchPushError, push_pull_request_branch


REQUEST = {
    "status": "CODING_PR_REQUEST_READY",
    "repo_root": "/repo",
    "head_branch": "feat/x",
    "head_sha": "a" * 40,
    "base_branch": "main",
    "title": "Title",
    "body": "Body",
    "request_sha256": "b" * 64,
}


def _state() -> dict:
    return {
        "head_sha": "a" * 40,
        "branch": "feat/x",
        "dirty_status": "CLEAN",
    }


def _proc(code: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


def _bind_common(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, bool]]:
    monkeypatch.setattr(push, "build_pull_request_request", lambda *a, **k: dict(REQUEST))
    monkeypatch.setattr(push, "_local_origin_repo", lambda repo_root: "owner/repo")
    monkeypatch.setattr(push, "inspect_repository", lambda *a, **k: _state())
    monkeypatch.setattr(push, "_remote_branch_sha", lambda repo, branch: "a" * 40)
    calls: list[tuple[str, bool]] = []

    def fake_push(repo_root: str, *, refspec: str, dry_run: bool, timeout_seconds: int = 120):
        calls.append((refspec, dry_run))
        return _proc(0, "ok", "")

    monkeypatch.setattr(push, "_git_push", fake_push)
    monkeypatch.setattr(push, "utc_now", lambda: "2026-10-09T18:00:00Z")
    return calls


def _invoke() -> dict:
    return push_pull_request_branch(
        "/repo",
        remote_repo="owner/repo",
        expected_head_sha="a" * 40,
        head_branch="feat/x",
        base_branch="main",
        title="Title",
        body="Body",
        expected_request_sha256="b" * 64,
    )


def test_push_binds_exact_pr_request_and_uses_non_force_sha_refspec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _bind_common(monkeypatch)

    result = _invoke()

    assert calls == [
        (f"{'a' * 40}:refs/heads/feat/x", True),
        (f"{'a' * 40}:refs/heads/feat/x", False),
    ]
    assert result["status"] == "GITHUB_BRANCH_PUSH_OK"
    assert result["remote_repo"] == "owner/repo"
    assert result["local_origin_repo"] == "owner/repo"
    assert result["request_sha256"] == "b" * 64
    assert result["remote_head_sha"] == "a" * 40
    assert result["force_used"] is False
    assert result["invariants"]["dry_run_passed"] is True
    assert result["invariants"]["remote_head_exact"] is True


def test_request_hash_drift_fails_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _bind_common(monkeypatch)
    monkeypatch.setattr(
        push,
        "build_pull_request_request",
        lambda *a, **k: {**REQUEST, "request_sha256": "c" * 64},
    )

    with pytest.raises(GitHubBranchPushError) as exc:
        _invoke()

    assert exc.value.status == "ERROR_GITHUB_PUSH_REQUEST_DRIFT"
    assert calls == []


def test_origin_identity_mismatch_fails_before_push(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _bind_common(monkeypatch)
    monkeypatch.setattr(push, "_local_origin_repo", lambda repo_root: "other/repo")

    with pytest.raises(GitHubBranchPushError) as exc:
        _invoke()

    assert exc.value.status == "ERROR_GITHUB_PUSH_ORIGIN_MISMATCH"
    assert calls == []


def test_local_state_drift_fails_before_push(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _bind_common(monkeypatch)
    monkeypatch.setattr(
        push,
        "inspect_repository",
        lambda *a, **k: {**_state(), "dirty_status": "DIRTY"},
    )

    with pytest.raises(GitHubBranchPushError) as exc:
        _invoke()

    assert exc.value.status == "ERROR_GITHUB_PUSH_LOCAL_STATE_DRIFT"
    assert calls == []


def test_dry_run_rejection_prevents_real_push(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bind_common(monkeypatch)
    calls: list[bool] = []

    def reject(repo_root: str, *, refspec: str, dry_run: bool, timeout_seconds: int = 120):
        calls.append(dry_run)
        return _proc(1, "", "non-fast-forward")

    monkeypatch.setattr(push, "_git_push", reject)

    with pytest.raises(GitHubBranchPushError) as exc:
        _invoke()

    assert exc.value.status == "ERROR_GITHUB_PUSH_DRY_RUN_REJECTED"
    assert calls == [True]


def test_post_push_remote_head_drift_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bind_common(monkeypatch)
    monkeypatch.setattr(push, "_remote_branch_sha", lambda repo, branch: "c" * 40)

    with pytest.raises(GitHubBranchPushError) as exc:
        _invoke()

    assert exc.value.status == "ERROR_GITHUB_PUSH_POSTCONDITION_HEAD"


def test_invalid_repo_and_hash_shapes_fail_before_request_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = {"builder": False}

    def builder(*args, **kwargs):
        called["builder"] = True
        return dict(REQUEST)

    monkeypatch.setattr(push, "build_pull_request_request", builder)

    with pytest.raises(GitHubBranchPushError) as repo_exc:
        push_pull_request_branch(
            "/repo",
            remote_repo="bad repo",
            expected_head_sha="a" * 40,
            head_branch="feat/x",
            base_branch="main",
            title="Title",
            body="Body",
            expected_request_sha256="b" * 64,
        )
    assert repo_exc.value.status == "ERROR_GITHUB_PUSH_REPO_INVALID"

    with pytest.raises(GitHubBranchPushError) as hash_exc:
        push_pull_request_branch(
            "/repo",
            remote_repo="owner/repo",
            expected_head_sha="a" * 40,
            head_branch="feat/x",
            base_branch="main",
            title="Title",
            body="Body",
            expected_request_sha256="not-a-hash",
        )
    assert hash_exc.value.status == "ERROR_GITHUB_PUSH_REQUEST_HASH_INVALID"
    assert called["builder"] is False
