"""Hermetic tests for authenticated push and pull-request submission (S4).

A local bare repository stands in for GitHub (origin keeps its plain github.com URL; git is told to transport to
the bare path) and a fake `gh` records exactly what it was asked. Nothing here touches the network.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from backend.app import coding_remote
from backend.app.cli import main
from backend.app.coding_lifecycle import _receipt_hash, build_pull_request_request
from backend.app.coding_remote import (
    CodingRemoteError,
    create_pull_request,
    push_branch,
    resolve_github_slug,
    verify_request,
)
from backend.app.coding_workspace import inspect_repository
from backend.app.permissions import grant_directory
from backend.app.tools import list_tool_audit_events, verify_tool_audit_chain
from backend.tests.test_coding_agent_hardening import granted_repo
from backend.tests.test_coding_workspace import git

SLUG = "acme/widgets"
PR_URL = f"https://github.com/{SLUG}/pull/42"


def attach_remote(tmp_path: Path, repo: Path, url: str = f"https://github.com/{SLUG}.git") -> Path:
    bare = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(bare))
    git(repo, "config", "remote.origin.url", url)
    git(repo, "config", f"url.{bare}.insteadOf", url)
    return bare


def commit_work(repo: Path, branch: str = "work/pr", text: str = "alpha\nbeta\n") -> str:
    if git(repo, "branch", "--show-current").strip() != branch:
        git(repo, "switch", "-c", branch)
    (repo / "tracked.txt").write_text(text, encoding="utf-8")
    git(repo, "commit", "-am", f"work {text!r}")
    return git(repo, "rev-parse", "HEAD").strip()


def build_request(repo: Path, cfg: Any, *, title: str = "Add beta", body: str = "Body text") -> dict[str, Any]:
    state = inspect_repository(repo, cfg=cfg)
    return build_pull_request_request(
        repo, expected_head_sha=state["head_sha"], head_branch=state["branch"], base_branch="main", title=title, body=body, cfg=cfg
    )


def install_fake_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, exit_code: int = 0, stdout: str = PR_URL + "\n", stderr: str = "") -> Path:
    """A `gh` on PATH that records its argv and stdin, then answers as configured."""
    bin_dir = tmp_path / "ghbin"
    bin_dir.mkdir()
    log = tmp_path / "gh_calls.jsonl"
    script = bin_dir / "gh"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "body = sys.stdin.read()\n"
        f"open({str(log)!r}, 'a').write(json.dumps({{'argv': sys.argv[1:], 'stdin': body}}) + '\\n')\n"
        f"sys.stderr.write({stderr!r})\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return log


def gh_calls(log: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


# --- the happy path -------------------------------------------------------------------------------------------


def test_push_then_pull_request_are_bound_to_the_preserved_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, repo = granted_repo(tmp_path)
    bare = attach_remote(tmp_path, repo)
    head = commit_work(repo)
    log = install_fake_gh(tmp_path, monkeypatch)
    request = build_request(repo, cfg)

    pushed = push_branch(repo, request, cfg=cfg)
    assert pushed["status"] == "CODING_PUSH_DONE" and pushed["remote_sha"] == head and pushed["repo_slug"] == SLUG and pushed["force"] is False
    assert git(bare, "rev-parse", "refs/heads/work/pr").strip() == head

    created = create_pull_request(repo, request, cfg=cfg)
    assert created["status"] == "CODING_PR_CREATED" and created["pr_url"] == PR_URL and created["pr_number"] == 42 and created["draft"] is True
    assert gh_calls(log) == [
        {"argv": ["pr", "create", "--repo", SLUG, "--base", "main", "--head", "work/pr", "--title", "Add beta", "--body-file", "-", "--draft"], "stdin": "Body text"}
    ]
    for kind in ("push", "pr"):  # the remote receipts are preserved, sealed and bound to the request
        saved = json.loads(coding_remote.receipt_path(cfg, kind, request).read_text(encoding="utf-8"))
        assert saved["request_sha256"] == request["request_sha256"] and saved["network_action_performed"] is True
        assert saved["receipt_sha256"] == _receipt_hash({k: v for k, v in saved.items() if k != "receipt_sha256"})
        assert "never reads" in saved["credential_handling"]


def test_ready_flag_omits_draft_and_a_second_creation_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, repo = granted_repo(tmp_path)
    attach_remote(tmp_path, repo)
    commit_work(repo)
    log = install_fake_gh(tmp_path, monkeypatch)
    request = build_request(repo, cfg)
    push_branch(repo, request, cfg=cfg)

    created = create_pull_request(repo, request, cfg=cfg, draft=False)
    assert created["draft"] is False and "--draft" not in gh_calls(log)[0]["argv"]

    with pytest.raises(CodingRemoteError) as info:
        create_pull_request(repo, request, cfg=cfg)
    assert info.value.status == "ERROR_CODING_PR_ALREADY_CREATED" and info.value.detail == PR_URL
    assert len(gh_calls(log)) == 1  # no duplicate pull request


def test_dry_runs_change_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, repo = granted_repo(tmp_path)
    bare = attach_remote(tmp_path, repo)
    commit_work(repo)
    log = install_fake_gh(tmp_path, monkeypatch)
    request = build_request(repo, cfg)

    pushed = push_branch(repo, request, cfg=cfg, dry_run=True)
    assert pushed["status"] == "CODING_PUSH_DRY_RUN" and pushed["network_action_performed"] is False and "--force" not in pushed["argv"]
    planned = create_pull_request(repo, request, cfg=cfg, dry_run=True)
    assert planned["status"] == "CODING_PR_CREATE_DRY_RUN" and planned["argv"][1:3] == ["pr", "create"]

    assert gh_calls(log) == [] and not (cfg.export_dir / "coding_remote").exists()
    assert git(bare, "branch", "--list").strip() == ""  # nothing was pushed


# --- the request artifact is what authorises the action ---------------------------------------------------------


def test_a_tampered_request_is_refused(tmp_path: Path) -> None:
    cfg, repo = granted_repo(tmp_path)
    attach_remote(tmp_path, repo)
    commit_work(repo)
    request = build_request(repo, cfg)

    for field, value in (("title", "Something else"), ("head_branch", "other"), ("body", "changed")):
        with pytest.raises(CodingRemoteError) as info:
            push_branch(repo, {**request, field: value}, cfg=cfg)
        assert info.value.status == "ERROR_CODING_REMOTE_REQUEST_TAMPERED"
    for not_a_request in ({}, {**request, "status": "SOMETHING_ELSE"}, "text", None):
        with pytest.raises(CodingRemoteError) as info:
            verify_request(not_a_request)
        assert info.value.status in {"ERROR_CODING_REMOTE_REQUEST_INVALID", "ERROR_CODING_REMOTE_REQUEST_TAMPERED"}


def test_drift_and_protected_heads_are_refused_before_any_network_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, repo = granted_repo(tmp_path)
    bare = attach_remote(tmp_path, repo)
    commit_work(repo)
    log = install_fake_gh(tmp_path, monkeypatch)
    request = build_request(repo, cfg)

    (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(CodingRemoteError) as info:
        push_branch(repo, request, cfg=cfg)
    assert info.value.status == "ERROR_CODING_PR_DIRTY_REPOSITORY"
    git(repo, "checkout", "--", "tracked.txt")

    commit_work(repo, text="alpha\nbeta\ngamma\n")  # HEAD moves on after the request was made
    with pytest.raises(CodingRemoteError) as info:
        push_branch(repo, request, cfg=cfg)
    assert info.value.status == "ERROR_CODING_PR_HEAD_DRIFT"

    forged = {key: value for key, value in request.items() if key != "request_sha256"}
    forged["head_branch"] = "main"  # an intact hash does not make a protected head acceptable
    forged["request_sha256"] = _receipt_hash(forged)
    with pytest.raises(CodingRemoteError) as info:
        push_branch(repo, forged, cfg=cfg)
    assert info.value.status == "ERROR_CODING_PROTECTED_BRANCH"

    assert git(bare, "branch", "--list").strip() == "" and gh_calls(log) == []


def test_a_request_is_bound_to_the_repository_it_was_made_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, repo = granted_repo(tmp_path)
    commit_work(repo)
    log = install_fake_gh(tmp_path, monkeypatch)
    request = build_request(repo, cfg)
    # Same commit, same branch, clean tree, but a different checkout: rebuilding succeeds yet cannot reproduce the hash.
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "--quiet", str(repo), str(clone))
    git(clone, "config", "remote.origin.url", f"https://github.com/{SLUG}.git")
    grant_directory(clone, cfg, granted_by="test", reason="clone of the repository")
    assert git(clone, "rev-parse", "HEAD").strip() == request["head_sha"]

    for action in (push_branch, create_pull_request):
        with pytest.raises(CodingRemoteError) as info:
            action(clone, request, cfg=cfg)
        assert info.value.status == "ERROR_CODING_REMOTE_REQUEST_STALE"
    assert gh_calls(log) == []


def test_a_push_the_remote_does_not_confirm_is_not_reported_as_done(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, repo = granted_repo(tmp_path)
    attach_remote(tmp_path, repo)
    commit_work(repo)
    request = build_request(repo, cfg)
    monkeypatch.setattr(coding_remote, "remote_branch_sha", lambda root, branch: "0" * 40)  # git "succeeded", the remote disagrees

    with pytest.raises(CodingRemoteError) as info:
        push_branch(repo, request, cfg=cfg)

    assert info.value.status == "ERROR_CODING_PUSH_REMOTE_SHA_MISMATCH"
    assert not coding_remote.receipt_path(cfg, "push", request).exists()


@pytest.mark.parametrize(
    ("url", "slug"),
    [
        ("https://github.com/acme/widgets.git", "acme/widgets"),
        ("https://github.com/acme/widgets", "acme/widgets"),
        ("https://github.com/acme/widgets/", "acme/widgets"),
        ("git@github.com:acme/widgets.git", "acme/widgets"),
        ("ssh://git@github.com/acme/widgets", "acme/widgets"),
        ("https://github.com/acme/my.repo-name_1.git", "acme/my.repo-name_1"),
    ],
)
def test_github_remote_urls_are_parsed_from_the_raw_config(tmp_path: Path, url: str, slug: str) -> None:
    _, repo = granted_repo(tmp_path)
    git(repo, "config", "remote.origin.url", url)
    assert resolve_github_slug(repo) == slug


def test_other_hosts_and_credentialed_urls_are_refused_without_echoing_them(tmp_path: Path) -> None:
    cfg, repo = granted_repo(tmp_path)
    commit_work(repo)
    request = build_request(repo, cfg)
    for url in ("https://gitlab.com/acme/widgets.git", "https://user:s3cretvalue@github.com/acme/widgets.git", "https://github.com.evil.example/acme/widgets.git", "file:///tmp/x"):
        git(repo, "config", "remote.origin.url", url)
        with pytest.raises(CodingRemoteError) as info:
            push_branch(repo, request, cfg=cfg)
        assert info.value.status == "ERROR_CODING_REMOTE_HOST_UNSUPPORTED" and "s3cretvalue" not in info.value.detail

    git(repo, "config", "--unset", "remote.origin.url")
    with pytest.raises(CodingRemoteError) as info:
        push_branch(repo, request, cfg=cfg)
    assert info.value.status == "ERROR_CODING_REMOTE_NOT_CONFIGURED"


# --- the remote must hold exactly the reviewed commit ------------------------------------------------------------


def test_pull_request_needs_the_exact_commit_on_the_remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, repo = granted_repo(tmp_path)
    attach_remote(tmp_path, repo)
    commit_work(repo)
    log = install_fake_gh(tmp_path, monkeypatch)
    request = build_request(repo, cfg)

    with pytest.raises(CodingRemoteError) as info:
        create_pull_request(repo, request, cfg=cfg)
    assert info.value.status == "ERROR_CODING_PR_REMOTE_BRANCH_MISSING"

    push_branch(repo, request, cfg=cfg)
    commit_work(repo, text="alpha\nbeta\ngamma\n")  # a newer commit that was never pushed
    newer = build_request(repo, cfg, title="Newer")
    with pytest.raises(CodingRemoteError) as info:
        create_pull_request(repo, newer, cfg=cfg)
    assert info.value.status == "ERROR_CODING_PR_REMOTE_BRANCH_MISMATCH"
    assert gh_calls(log) == []


def test_push_never_forces_and_surfaces_a_rejection(tmp_path: Path) -> None:
    cfg, repo = granted_repo(tmp_path)
    bare = attach_remote(tmp_path, repo)
    first = commit_work(repo)
    push_branch(repo, build_request(repo, cfg), cfg=cfg)

    git(repo, "reset", "--hard", "HEAD~1")
    diverged = commit_work(repo, text="alpha\nsomething else\n")
    assert diverged != first
    with pytest.raises(CodingRemoteError) as info:
        push_branch(repo, build_request(repo, cfg, title="Diverged"), cfg=cfg)

    assert info.value.status == "ERROR_CODING_PUSH_FAILED"
    assert git(bare, "rev-parse", "refs/heads/work/pr").strip() == first  # the remote history was not rewritten


# --- gh problems are never success-shaped -------------------------------------------------------------------------


def test_missing_gh_is_reported_and_nothing_is_pushed_or_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, repo = granted_repo(tmp_path)
    attach_remote(tmp_path, repo)
    commit_work(repo)
    request = build_request(repo, cfg)
    push_branch(repo, request, cfg=cfg)
    real_which = coding_remote.shutil.which
    monkeypatch.setattr(coding_remote.shutil, "which", lambda name, *a, **k: None if name == "gh" else real_which(name, *a, **k))

    with pytest.raises(CodingRemoteError) as info:
        create_pull_request(repo, request, cfg=cfg)
    assert info.value.status == "ERROR_CODING_PR_GH_UNAVAILABLE" and "gh auth login" in info.value.detail


@pytest.mark.parametrize(
    ("exit_code", "stdout", "stderr", "status"),
    [
        (1, "", "GraphQL: Resource not accessible. token ghp_" + "A" * 36, "ERROR_CODING_PR_CREATE_FAILED"),
        (0, "all done, no url here\n", "", "ERROR_CODING_PR_CREATE_UNPARSEABLE"),
        (0, "https://github.com/someone-else/other/pull/7\n", "", "ERROR_CODING_PR_CREATE_UNPARSEABLE"),
    ],
    ids=["gh-fails", "no-url", "url-for-another-repository"],
)
def test_gh_failures_are_surfaced_and_leave_no_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int, stdout: str, stderr: str, status: str
) -> None:
    cfg, repo = granted_repo(tmp_path)
    attach_remote(tmp_path, repo)
    commit_work(repo)
    install_fake_gh(tmp_path, monkeypatch, exit_code=exit_code, stdout=stdout, stderr=stderr)
    request = build_request(repo, cfg)
    push_branch(repo, request, cfg=cfg)

    with pytest.raises(CodingRemoteError) as info:
        create_pull_request(repo, request, cfg=cfg)

    assert info.value.status == status
    assert "ghp_" + "A" * 36 not in info.value.detail  # token-shaped text never reaches the terminal
    assert not coding_remote.receipt_path(cfg, "pr", request).exists()  # so a corrected retry is possible


# --- the CLI: separate approvals, an audit trail, and nothing runs without a yes -----------------------------------


def test_cli_push_and_pr_create_need_approval_and_are_audited(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, repo = granted_repo(tmp_path)
    bare = attach_remote(tmp_path, repo)
    commit_work(repo)
    log = install_fake_gh(tmp_path, monkeypatch)
    request_file = tmp_path / "request.json"

    assert main(["code", "pr-request", "--repo", str(repo), "--title", "Add beta", "--body", "Body text", "--output", str(request_file)], cfg) == 0
    printed = json.loads(capsys.readouterr().out)
    assert json.loads(request_file.read_text(encoding="utf-8")) == printed and printed["status"] == "CODING_PR_REQUEST_READY"

    push = ["code", "push", "--repo", str(repo), "--request", str(request_file)]
    create = ["code", "pr-create", "--repo", str(repo), "--request", str(request_file)]

    assert main([*push, "--dry-run"], cfg) == 0  # a dry run needs no approval: nothing is touched
    assert json.loads(capsys.readouterr().out)["status"] == "CODING_PUSH_DRY_RUN"

    assert main(push, cfg) == 2
    assert json.loads(capsys.readouterr().err)["status"] == "ERROR_CODING_CONFIRMATION_REQUIRED"
    assert git(bare, "branch", "--list").strip() == ""
    assert main([*push, "--yes"], cfg) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "CODING_PUSH_DONE"

    assert main(create, cfg) == 2
    assert json.loads(capsys.readouterr().err)["status"] == "ERROR_CODING_CONFIRMATION_REQUIRED" and gh_calls(log) == []
    assert main([*create, "--yes"], cfg) == 0
    assert json.loads(capsys.readouterr().out)["pr_url"] == PR_URL and len(gh_calls(log)) == 1

    requested, approved, executed, denied = "TOOL_CALL_REQUESTED", "TOOL_CALL_APPROVED", "TOOL_CALL_EXECUTED", "TOOL_CALL_DENIED"
    assert [(e["tool_name"], e["event"]) for e in list_tool_audit_events(cfg)["events"]] == [
        ("coding.push", requested), ("coding.push", denied), ("coding.push", requested), ("coding.push", approved), ("coding.push", executed),
        ("coding.pr_create", requested), ("coding.pr_create", denied), ("coding.pr_create", requested), ("coding.pr_create", approved), ("coding.pr_create", executed),
    ]
    assert verify_tool_audit_chain(cfg)["status"] == "TOOL_AUDIT_CHAIN_OK"


def test_cli_rejects_a_missing_or_corrupt_request_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg, repo = granted_repo(tmp_path)
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")

    for request in (tmp_path / "missing.json", broken):
        assert main(["code", "push", "--repo", str(repo), "--request", str(request), "--yes"], cfg) == 2
        assert json.loads(capsys.readouterr().err)["status"] == "ERROR_CODING_REMOTE_REQUEST_INVALID"
