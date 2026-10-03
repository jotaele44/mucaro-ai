from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.cli import main
from backend.app.permissions import grant_directory
from backend.tests.test_coding_workspace import git, make_repo, make_settings


def external_repo(tmp_path: Path):
    app_root = tmp_path / "app"
    app_root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    return make_settings(app_root), make_repo(external)


def run(capsys: pytest.CaptureFixture[str], cfg, *argv: str) -> tuple[int, dict]:
    code = main(list(argv), cfg)
    captured = capsys.readouterr()
    return code, json.loads(captured.out or captured.err)


def make_patch(tmp_path: Path, repo: Path) -> tuple[Path, str]:
    (repo / "tracked.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    patch = git(repo, "diff", "HEAD")
    git(repo, "checkout", "--", "tracked.txt")
    patch_file = tmp_path / "change.patch"
    patch_file.write_text(patch, encoding="utf-8")
    return patch_file, patch


def test_code_commands_status_read_diff_and_patch_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg, repo = external_repo(tmp_path)
    grant_directory(repo, cfg, granted_by="test", reason="cli code test")
    (repo / "tracked.txt").write_text("alpha\nbeta\n", encoding="utf-8")

    code, status = run(capsys, cfg, "code", "status", "--repo", str(repo))
    assert code == 0 and status["status"] == "CODING_REPOSITORY_STATUS_OK"

    code, read = run(capsys, cfg, "code", "read", "tracked.txt", "--repo", str(repo))
    assert code == 0 and read["content"] == "alpha\nbeta\n"

    code, diff = run(capsys, cfg, "code", "diff", "--repo", str(repo))
    assert code == 0 and "tracked.txt" in diff["changed_files"]

    patch = git(repo, "diff", "HEAD")
    git(repo, "checkout", "--", "tracked.txt")
    patch_file = tmp_path / "change.patch"
    patch_file.write_text(patch, encoding="utf-8")
    code, checked = run(capsys, cfg, "code", "check-patch", str(patch_file), "--repo", str(repo))
    assert code == 0 and (repo / "tracked.txt").read_text(encoding="utf-8") == "alpha\n"
    assert "tracked.txt" in json.dumps(checked)


def test_code_commands_reject_ungranted_repo_and_escape(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg, repo = external_repo(tmp_path)

    code, error = run(capsys, cfg, "code", "status", "--repo", str(repo))
    assert code == 2 and error["status"] == "ERROR_DIRECTORY_PERMISSION_REQUIRED"

    grant_directory(repo, cfg, granted_by="test", reason="cli code test")
    code, error = run(capsys, cfg, "code", "read", "../outside.txt", "--repo", str(repo))
    assert code == 2 and error["status"].startswith("ERROR_")


def test_code_apply_branch_commit_require_approval_and_work(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg, repo = external_repo(tmp_path)
    grant_directory(repo, cfg, granted_by="test", reason="cli code test")
    (repo / "tracked.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    patch = git(repo, "diff", "HEAD")
    git(repo, "checkout", "--", "tracked.txt")
    patch_file = tmp_path / "change.patch"
    patch_file.write_text(patch, encoding="utf-8")

    code, error = run(capsys, cfg, "code", "apply", str(patch_file), "--repo", str(repo))
    assert code == 2 and error["status"] == "ERROR_CODING_CONFIRMATION_REQUIRED"
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "alpha\n"

    code, _ = run(capsys, cfg, "code", "branch", "work/cli", "--repo", str(repo), "--yes")
    assert code == 0 and git(repo, "branch", "--show-current").strip() == "work/cli"

    code, _ = run(capsys, cfg, "code", "apply", str(patch_file), "--repo", str(repo), "--yes")
    assert code == 0 and (repo / "tracked.txt").read_text(encoding="utf-8") == "alpha\nbeta\n"

    code, _ = run(capsys, cfg, "code", "commit", "--path", "tracked.txt", "-m", "add beta", "--repo", str(repo), "--yes")
    assert code == 0 and "add beta" in git(repo, "log", "-1", "--format=%s")


def test_code_agent_scripted_loop_gates_mutation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg, repo = external_repo(tmp_path)
    grant_directory(repo, cfg, granted_by="test", reason="cli code test")
    (repo / "tracked.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    patch = git(repo, "diff", "HEAD")
    git(repo, "checkout", "--", "tracked.txt")
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps(
            [
                {"tool": "read", "args": {"path": "tracked.txt"}},
                "not json",
                {"tool": "apply_patch", "args": {"patch": patch}},
                {"final": "done"},
            ]
        ),
        encoding="utf-8",
    )

    code, denied = run(capsys, cfg, "code", "agent", "add beta", "--repo", str(repo), "--script", str(script))
    assert code == 0 and denied["status"] == "AGENT_COMPLETED"
    assert [s.get("outcome_status") for s in denied["steps"] if s.get("tool") == "apply_patch"] == ["ERROR_AGENT_APPROVAL_DENIED"]
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "alpha\n"

    code, approved = run(capsys, cfg, "code", "agent", "add beta", "--repo", str(repo), "--script", str(script), "--yes")
    assert code == 0 and approved["transcript_sha256"]
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "alpha\nbeta\n"


def test_code_agent_step_limit_and_bad_provider(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg, repo = external_repo(tmp_path)
    grant_directory(repo, cfg, granted_by="test", reason="cli code test")
    script = tmp_path / "s.json"
    script.write_text(json.dumps([{"tool": "status"}] * 3), encoding="utf-8")
    code, out = run(capsys, cfg, "code", "agent", "x", "--repo", str(repo), "--script", str(script), "--max-steps", "2")
    assert code == 0 and out["status"] == "AGENT_STEP_LIMIT_REACHED" and out["step_count"] == 2

    code, err = run(capsys, cfg, "code", "agent", "x", "--repo", str(repo), "--provider", "mock")
    assert code == 2 and err["status"] == "ERROR_AGENT_PROVIDER_UNSUPPORTED"


def test_code_agent_local_provider_rejects_non_loopback_and_drives_loop(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import http.server
    import threading

    cfg, repo = external_repo(tmp_path)
    grant_directory(repo, cfg, granted_by="test", reason="cli code test")

    monkeypatch.setenv("MUCARO_AI_LOCAL_MODEL_URL", "http://example.com/v1")
    code, err = run(capsys, cfg, "code", "agent", "x", "--repo", str(repo))
    assert code == 2 and err["status"] == "ERROR_LOCAL_MODEL_URL_NOT_LOOPBACK"

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            body = json.dumps({"choices": [{"message": {"content": json.dumps({"final": "no changes needed"})}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("MUCARO_AI_LOCAL_MODEL_URL", f"http://127.0.0.1:{server.server_address[1]}/v1")
        code, out = run(capsys, cfg, "code", "agent", "check", "--repo", str(repo))
        assert code == 0 and out["status"] == "AGENT_COMPLETED" and out["final"] == "no changes needed"
    finally:
        server.shutdown()


def test_code_agent_session_resume_and_chat_repl(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    cfg, repo = external_repo(tmp_path)
    grant_directory(repo, cfg, granted_by="test", reason="cli code test")
    seen: list[int] = []
    import backend.app.coding_agent as agent_mod

    real = agent_mod.run_agent

    def spy(goal, repo_arg, **kw):
        seen.append(len(kw.get("history") or []))
        return real(goal, repo_arg, **kw)

    monkeypatch.setattr("backend.app.cli_code.run_agent", spy)
    script = tmp_path / "s.json"
    script.write_text(json.dumps([{"final": "one"}, {"final": "two"}, {"final": "three"}]), encoding="utf-8")

    code, first = run(capsys, cfg, "code", "agent", "g1", "--repo", str(repo), "--script", str(script), "--session", "s1")
    assert code == 0 and first["resumed"] is False and "messages" not in first
    code, second = run(capsys, cfg, "code", "agent", "g2", "--script", str(script), "--session", "s1")
    assert code == 0 and second["resumed"] is True and second["repo"] == str(repo)
    assert seen == [0, 3]

    code, listed = run(capsys, cfg, "code", "sessions")
    assert listed["sessions"] == [{"session_id": "s1", "repo": str(repo), "turns": 2}]

    monkeypatch.setattr("sys.stdin", io.StringIO("g3\n\n/exit\n"))
    assert main(["code", "chat", "--script", str(script), "--session", "s1"], cfg) == 0
    assert capsys.readouterr().out == "one\n"
    code, listed = run(capsys, cfg, "code", "sessions")
    assert listed["sessions"][0]["turns"] == 3

    code, bad = run(capsys, cfg, "code", "agent", "x", "--script", str(script), "--session", "../evil")
    assert code == 2 and bad["status"] == "ERROR_AGENT_SESSION_ID_INVALID"


def test_code_checks_plan_pr_request_and_run_gate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg, repo = external_repo(tmp_path)
    grant_directory(repo, cfg, granted_by="test", reason="cli code test")

    code, err = run(capsys, cfg, "code", "checks", "--repo", str(repo), "--image", "img@sha256:" + "a" * 64, "--check", "bogus")
    assert code == 2 and err["status"] == "ERROR_CODING_CHECK_SPEC"

    code, err = run(capsys, cfg, "code", "checks", "--repo", str(repo), "--image", "img@sha256:" + "a" * 64, "--check", "test:unit=pytest -q", "--run")
    assert code == 2 and err["status"] == "ERROR_CODING_CONFIRMATION_REQUIRED"

    code, plan = run(capsys, cfg, "code", "checks", "--repo", str(repo), "--image", "img@sha256:" + "a" * 64, "--check", "test:unit=pytest -q")
    assert "status" in plan

    git(repo, "switch", "-c", "work/pr")
    code, pr = run(capsys, cfg, "code", "pr-request", "--repo", str(repo), "--title", "T", "--body", "B")
    assert code == 0 and pr["status"] == "CODING_PR_REQUEST_READY" and pr["network_action_performed"] is False
    assert pr["head_branch"] == "work/pr" and pr["base_branch"] == "main"


def test_code_agent_web_fetch_needs_approval_and_blocks_private(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg, repo = external_repo(tmp_path)
    grant_directory(repo, cfg, granted_by="test", reason="cli code test")
    script = tmp_path / "w.json"
    script.write_text(json.dumps([{"tool": "web_fetch", "args": {"url": "http://127.0.0.1:9/x"}}, {"final": "ok"}]), encoding="utf-8")

    _, denied = run(capsys, cfg, "code", "agent", "g", "--repo", str(repo), "--script", str(script))
    assert denied["steps"][0]["outcome_status"] == "ERROR_AGENT_APPROVAL_DENIED"

    exit_code, blocked = run(capsys, cfg, "code", "agent", "g", "--repo", str(repo), "--script", str(script), "--yes")
    assert exit_code == 0
    assert blocked["steps"][0]["approved"] is True
    assert blocked["steps"][0]["outcome_status"].startswith("ERROR_")
    assert blocked["steps"][0]["outcome_status"] != "ERROR_AGENT_APPROVAL_DENIED"


def test_code_agent_web_search_gated_and_requires_live_opt_in(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg, repo = external_repo(tmp_path)
    grant_directory(repo, cfg, granted_by="test", reason="cli code test")
    script = tmp_path / "ws.json"
    script.write_text(json.dumps([{"tool": "web_search", "args": {"query": "mucaro"}}, {"final": "ok"}]), encoding="utf-8")

    _, denied = run(capsys, cfg, "code", "agent", "g", "--repo", str(repo), "--script", str(script))
    assert denied["steps"][0]["outcome_status"] == "ERROR_AGENT_APPROVAL_DENIED"

    _, approved = run(capsys, cfg, "code", "agent", "g", "--repo", str(repo), "--script", str(script), "--yes")
    assert approved["steps"][0]["outcome_status"] == "ERROR_WEB_SEARCH_LIVE_NOT_ALLOWED"


def test_code_agent_delegate_is_read_only_and_depth_limited(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg, repo = external_repo(tmp_path)
    grant_directory(repo, cfg, granted_by="test", reason="cli code test")
    _, patch = make_patch(tmp_path, repo)
    # Replies are consumed in order by parent, then sub-agent, sharing one scripted model.
    script = tmp_path / "d.json"
    script.write_text(
        json.dumps(
            [
                {"tool": "delegate", "args": {"goal": "inspect"}},
                {"tool": "apply_patch", "args": {"patch": patch}},
                {"tool": "delegate", "args": {"goal": "nested"}},
                {"final": "sub done"},
                {"final": "parent done"},
            ]
        ),
        encoding="utf-8",
    )
    _, out = run(capsys, cfg, "code", "agent", "g", "--repo", str(repo), "--script", str(script), "--yes")
    assert out["status"] == "AGENT_COMPLETED" and out["final"] == "parent done"
    assert out["steps"][0]["outcome_status"] == "SUBAGENT_AGENT_COMPLETED"
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "alpha\n"


def test_code_agent_mcp_call_is_approval_gated(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import sys

    cfg, repo = external_repo(tmp_path)
    grant_directory(repo, cfg, granted_by="test", reason="cli code test")
    fake = str(Path(__file__).with_name("fake_mcp_server.py"))
    (cfg.data_dir / "mcp_servers.json").write_text(json.dumps({"servers": {"fake": {"command": [sys.executable, fake]}}}), encoding="utf-8")
    script = tmp_path / "m.json"
    script.write_text(json.dumps([{"tool": "mcp_call", "args": {"server": "fake", "tool": "echo", "args": {"text": "x"}}}, {"final": "ok"}]), encoding="utf-8")

    _, denied = run(capsys, cfg, "code", "agent", "g", "--repo", str(repo), "--script", str(script))
    assert denied["steps"][0]["outcome_status"] == "ERROR_AGENT_APPROVAL_DENIED"
    _, ok = run(capsys, cfg, "code", "agent", "g", "--repo", str(repo), "--script", str(script), "--yes")
    assert ok["steps"][0]["outcome_status"] == "MCP_TOOL_OK"
