from __future__ import annotations

import subprocess
import sys

import pytest

import scripts.certify_fresh_clone as certify_fresh_clone
import scripts.launch_app as launch_app


def test_launch_app_self_test():
    proc = subprocess.run(
        [sys.executable, "scripts/launch_app.py", "--self-test"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert proc.returncode == 0
    assert "LAUNCH_APP_SELF_TEST_OK" in proc.stdout


def test_package_desktop_self_test():
    proc = subprocess.run(
        [sys.executable, "scripts/package_desktop.py", "--self-test"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert proc.returncode == 0
    assert "DESKTOP_PACKAGE_READY" in proc.stdout


def test_fresh_clone_uses_shallow_branch_clone_for_named_refs(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, *, cwd, check=True):
        del check
        calls.append((cmd, cwd))
        return subprocess.CompletedProcess(cmd, 0, "")

    monkeypatch.setattr(certify_fresh_clone, "run", fake_run)

    clone_dir = tmp_path / "mucaro-ai"
    certify_fresh_clone.clone_ref("https://github.com/example/mucaro-ai.git", "main", tmp_path, clone_dir)

    assert calls == [
        (
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                "main",
                "https://github.com/example/mucaro-ai.git",
                str(clone_dir),
            ],
            tmp_path,
        )
    ]


def test_fresh_clone_fetches_exact_commit_sha(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, *, cwd, check=True):
        del check
        calls.append((cmd, cwd))
        return subprocess.CompletedProcess(cmd, 0, "")

    monkeypatch.setattr(certify_fresh_clone, "run", fake_run)

    clone_dir = tmp_path / "mucaro-ai"
    commit_sha = "8e8cf79b03daebaf12caa51050f6143f52fc6e44"
    certify_fresh_clone.clone_ref("https://github.com/example/mucaro-ai.git", commit_sha, tmp_path, clone_dir)

    assert calls == [
        (["git", "clone", "--no-checkout", "https://github.com/example/mucaro-ai.git", str(clone_dir)], tmp_path),
        (["git", "fetch", "--depth", "1", "origin", commit_sha], clone_dir),
        (["git", "checkout", "--detach", commit_sha], clone_dir),
    ]


def test_launch_app_reports_missing_frontend_deps(tmp_path, monkeypatch):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(launch_app, "ROOT", tmp_path)

    with pytest.raises(SystemExit) as exc:
        launch_app.ensure_frontend_dist()

    assert str(exc.value) == "ERROR_FRONTEND_DEPS_MISSING run: cd frontend && npm ci"


def test_launch_app_rejects_non_mucaro_port_conflict(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["launch_app.py", "--smoke-test", "--no-browser", "--port", "54321"])
    monkeypatch.setattr(launch_app, "ensure_frontend_dist", lambda skip_build=False: None)
    monkeypatch.setattr(launch_app, "port_open", lambda port: True)
    monkeypatch.setattr(launch_app, "health_ok", lambda port: False)

    assert launch_app.main() == 5
    error_output = capsys.readouterr().err
    assert "ERROR_PORT_IN_USE_NON_MUCARO_AI port=54321" in error_output
